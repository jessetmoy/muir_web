import os
import csv
import logging
import re
import functools
import pprint as pp
import mw_settings as s
import raster_utils as ru
from os.path import join, isfile, splitext
from osgeo import gdal
from numpy import *  # not doing the usual import numpy as np because we want to keep subset_rule evaluation simple

gdal.UseExceptions()
seterr(divide='raise', over='print', under='print', invalid='raise')
elements = {}
relationships = {}
frequency_types = []
strength_types = []


# CLASSES

class Element(object):

    def __init__(self, obj):
        for attr, value in obj.items():
            self[attr] = value

        self.relationships = {}
        self.object_list = []

    def __setitem__(self, objkey, value):
        self.__dict__[objkey] = value

    def __repr__(self):
        return self.name

    @property
    def id_path(self):
        elementid = id_str(self.elementid)
        path = join(s.GRID_DIR, '%s.tif' % elementid)
        return path

    @property
    def status(self):
        if isfile(self.id_path):
            return True
        return False

    def set_relationships(self):
        self.object_list = []
        rel_dict = {}
        subject_rels = [r for r in relationships if r['id_subject'] == self.elementid]

        for r in subject_rels:
            state = r['state']
            group = r['relationshiptype']
            if state not in rel_dict.keys():
                rel_dict[state] = {}
            if group not in rel_dict[state].keys():
                rel_dict[state][group] = []

            # append the object element and its relationship to list keyed by state and group
            rel_dict[state][group].append({
                'id': r['id'],
                'obj': elements[r['id_object']],
                'rel': r,
            })

            if r['id_object'] not in self.object_list:
                self.object_list.append(elements[r['id_object']])

        self.relationships = rel_dict

    def show_relationships(self):
        self.set_relationships()
        logging.info(' '.join([str(self.elementid), self.name, 'requirements:']))
        logging.info('\n%s' % pp.pformat(self.relationships))

    def has_requirements(self):
        """
        check the status of objects in objects list, if all required grids exist
        return True, else return False
        :return: boolean
        """
        ro_false = []
        for o in self.object_list:
            r = get_relationship(self.elementid, o.elementid)
            if o.status is False and ('relationshiptype_label' not in r or
                                      r['relationshiptype_label'] != s.UNMAPPED_CONDITION):
                ro_false.append(o)

        if len(ro_false) == 0:
            # logging.info('All required objects exist for %s' % self.name)
            return True
        else:
            # logging.error('Unable to map %s [%s]; objects missing: %s' % (self.name, self.elementid, ro_false))
            return False


# UTILITIES

def api_headers(client=False):
    # If we need to authorize to get to the API, this is where we'd do it; 'Authorization': 'Bearer %s' % access_token
    if client:
        pass
    headers = {}
    api_head = {'params': s.params, 'headers': headers}
    if hasattr(s, 'http_auth'):
        api_head['auth'] = s.http_auth
    return api_head


def calc_grid(elementid):
    subject = elements[elementid]
    subject.set_relationships()

    if subject.has_requirements():
        logging.info('Mapping %s [%s]' % (subject.elementid, subject.name))
        try:
            if subject.mw_definition == s.COMBINATION:
                return combination(subject)
            elif subject.mw_definition == s.SUBSET:
                return subset(subject)
            elif subject.mw_definition == s.ADJACENCY:
                return adjacency(subject)
        except Exception as e:
            logging.exception('exception!')
            return False
    else:
        return False


def get_by_id(list_of_dicts, dictkey, prop):
    for d in list_of_dicts:
        if d['id'] == dictkey:
            return d[prop]
    return None


def get_maxprob(element):
    return float(get_by_id(frequency_types, element.frequencytype, 'maxprob')) or 100.0


def get_object(element):  # for subjects with a relationship (subset, adjacency) depending on a single object
    # state = element.relationships.keys()[0]
    # group = element.relationships[state].keys()[0]
    # return element.relationships[state][group][0]
    return element.object_list[0] or None


def get_relationship(id_subject, id_object):
    for r in relationships:
        if r['id_subject'] == id_subject and r['id_object'] == id_object:
            return r
    return None


def id_str(id_decimal):  # form is decimal, but datatype can be str
    return str(id_decimal).replace('.', '_')


def parse_calc(expression):
    dict_str = r"arrays['\1']"
    p = re.compile('\[([0|[1-9]\d*?\.\d+?(?<=\d))]')
    return p.sub(dict_str, expression)


def clear_automapped():
    for elementid, el in elements.items():
        if el.mapped_manually is False and el.status is True:
            try:
                os.remove(el.id_path)
            except OSError:
                logging.warning('Failed to clear %s' % el.id_path)


def write_csv(filename, headers, rows):
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


# MAPPING METHODS

def round_int(arr, nodata):
    newarr = floor(arr + 0.5).astype(int16)
    newarr = ma.where(newarr.data == nodata, s.NODATA_INT16, newarr.data)  # convert source nodata to int16 nodata
    newarr.set_fill_value(s.NODATA_INT16)
    return newarr


def union(object_list):
    if len(object_list) == 1:
        return object_list[0]
    # object_list = [i / 100.0 for i in object_list]
    u = functools.reduce(lambda x, y: x + y, object_list)
    # u *= 100
    u[u > 100] = 100
    return u


def intersection(object_list):
    if len(object_list) == 1:
        return object_list[0]
    object_list = [i / 100.0 for i in object_list]
    u = functools.reduce(lambda x, y: x * y, object_list)
    u *= 100
    u[u > 100] = 100
    return u


def combination(element):
    states = []
    habitat_mods = []
    geotransform = None
    projection = None
    nodata = None
    default_habitat = None

    for state in element.relationships:
        groups = []
        for group in element.relationships[state]:
            rasters = []
            for rel in element.relationships[state][group]:
                if ('relationshiptype_label' not in rel['rel'] or
                        rel['rel']['relationshiptype_label'] != s.UNMAPPED_CONDITION):
                    arr, geotransform, projection, nodata = ru.raster_to_ndarray(rel['obj'].id_path)
                    strength = float(get_by_id(strength_types, rel['rel']['strengthtype'], 'prob')) / 100
                    if default_habitat is None:
                        default_habitat = ma.copy(arr)
                        default_habitat[default_habitat >= 0] = 1

                    if rel['rel']['interactiontype'] == s.REQUIRED:
                        rasters.append(arr * strength)
                    elif rel['rel']['interactiontype'] == s.ENHANCING:
                        habitat_mods.append(100 + (arr * strength))
                    elif rel['rel']['interactiontype'] == s.ATTENUATING:
                        habitat_mods.append(100 - (arr * strength))

            if len(rasters) > 0:
                groups.append(union(rasters))

        if len(groups) > 0:
            states.append(intersection(groups))

    if len(states) > 0:
        habitat = union(states)
    else:
        habitat = default_habitat

    # habitat mods (enhancing/attenuating) are applied (intersected) after calculation of core habitat.
    # This means the states and groups of relationships for this interaction type are labels only;
    # the union/intersection logic they imply for core habitat does not apply to mods.
    habitat = intersection([habitat] + habitat_mods)

    # scale by prevalence
    habitat = habitat * get_maxprob(element) / 100

    out_raster = {
        'file': element.id_path,
        'geotransform': geotransform,
        'projection': projection,
        'nodata': s.NODATA_INT16
    }
    ru.ndarray_to_raster(round_int(habitat, nodata), out_raster)
    return True


def subset(element):
    """
    subset objects for use in where() can be float, but output is coerced to int16
    i.e. subset supports arbitrary map algebra with arbitrary units but always yields standard MW 0-100 int16
    element.subset_rule must adhere to gdalnumeric syntax using +-/* or any
    numpy array functions (e.g. logical_and()) (http://www.gdal.org/gdal_calc.html)
    and use [elementid] as placeholders in calc string
    https://stackoverflow.com/questions/3030480/numpy-array-how-to-select-indices-satisfying-multiple-conditions
    note on bitwise vs. logical:
    https://stackoverflow.com/questions/10377096/multiple-conditions-using-or-in-numpy-array
    Example: logical_and([31.00] >= 2, [31.00] <= 5)
    """
    arrays = {}
    geotransform = None
    projection = None
    present = None
    absent = None

    if len(element.object_list) > 0:
        calc_expression = parse_calc(element.subset_rule)

        # logging.info(element.elementid)
        for idx, obj in enumerate(element.object_list):
            # geotransform, projection set to those of last element in object_list
            arrays[obj.elementid], geotransform, projection, nodata = ru.raster_to_ndarray(obj.id_path)
            if idx == 0:
                # present/absent need both proper mask AND nodata vals in that mask
                # GDAL WriteArray() requires datatype-appropriate nodata values in the
                # underlying array data (i.e. it ignores mask)
                pa = arrays[obj.elementid].filled(s.NODATA_INT16).astype(int16)
                # logging.info('pa:')
                # logging.info(pa.dtype)
                # logging.info(pa)
                # logging.info(pa[5000][5000])
                pa = ma.masked_values(pa, s.NODATA_INT16)
                present = ma.where(pa.data != s.NODATA_INT16, 1, pa.data)
                absent = ma.where(pa.data != s.NODATA_INT16, 0, pa.data)

                # if element.elementid == '25.00':
                #     out = {
                #         'file': '%s/test.tif' % s.GRID_DIR,
                #         'geotransform': geotransform,
                #         'projection': projection,
                #         'nodata': s.NODATA_INT16
                #     }
                #     ru.ndarray_to_raster(absent, out)

        try:
            # logging.info(calc_expression)
            subset_array = ma.where(eval(calc_expression), present, absent)
            present = None
            absent = None
            # subset_array *= get_maxprob(element)
            subset_array = subset_array * get_maxprob(element)

            out_raster = {
                'file': element.id_path,
                'geotransform': geotransform,
                'projection': projection,
                'nodata': s.NODATA_INT16
            }

            ru.ndarray_to_raster(round_int(subset_array, nodata), out_raster)
            return True

        except KeyError as e:
            logging.error('{} in the subset rule is not an object of {} [{}]'.format(e, element.elementid,
                                                                                     element.name))
            return False


def adjacency(element):
    obj = get_object(element)
    if obj is not None:
        # http://www.gdal.org/gdal_proximity.html
        # http://arijolma.org/Geo-GDAL/1.6/class_geo_1_1_g_d_a_l.html#afa9a3fc598089b58eb23445b8c1c88b4
        options = ['MAXDIST=%s' % (element.adjacency_rule / s.CELL_SIZE),
                   'VALUES=%s' % ','.join(str(i) for i in range(1, 101)),
                   'FIXED_BUF_VAL=%s' % get_maxprob(element),
                   'USE_INPUT_NODATA=YES',
                   ]

        src_ds = gdal.Open(obj.id_path)
        src_band = src_ds.GetRasterBand(1)
        temp_path = '%s_temp%s' % (splitext(element.id_path)[0], splitext(element.id_path)[1])
        temp_ds = gdal.GetDriverByName(s.RASTER_DRIVER).CreateCopy(temp_path, src_ds, 0)
        temp_band = temp_ds.GetRasterBand(1)

        gdal.ComputeProximity(src_band, temp_band, options=options)
        # temp_band.FlushCache()
        src_ds = None
        temp_ds = None

        # unmask, preserve non-nodata from source object as 0, then remask
        # TODO: see if we can avoid writing temp path to disk and then reopening
        dst_arr, geotransform, projection, nodata = ru.raster_to_ndarray(temp_path)
        dst_arr = array(dst_arr.data)
        obj_arr, g, p, n = ru.raster_to_ndarray(obj.id_path)
        obj_arr = array(obj_arr.data)
        dst_arr = where(logical_and(obj_arr != nodata, dst_arr == nodata), 0, dst_arr).astype(int16)
        dst_arr = where(dst_arr == nodata, s.NODATA_INT16, dst_arr)  # convert source nodata to int16 nodata
        dst_arr = ma.masked_values(dst_arr, s.NODATA_INT16)

        out_raster = {
            'file': element.id_path,
            'geotransform': geotransform,
            'projection': projection,
            'nodata': s.NODATA_INT16
        }
        ru.ndarray_to_raster(dst_arr, out_raster)

        dst_arr = None
        obj_arr = None
        try:
            os.remove(temp_path)
        except OSError:
            logging.warning('Could not delete temp file %s' % temp_path)

        return True

    else:
        logging.warning('No adjacency object defined for %s [%s]' % (element.name, element.elementid))
        return False
