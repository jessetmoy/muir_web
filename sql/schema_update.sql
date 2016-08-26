﻿ -- DROP UNUSED ELEMENT ATTRIBUTES

ALTER TABLE welikia_mw_element
DROP COLUMN mw_path,
DROP COLUMN mw_gridname,
DROP COLUMN externallink,
DROP COLUMN spatialsource,
DROP COLUMN notes,
DROP COLUMN lifetype;

-- DROP UNUSED RELATIONSHIP ATTIBUTES
ALTER TABLE welikia_mw_relationship
DROP COLUMN explicit,
DROP COLUMN spatialrelationship,
DROP COLUMN spatialcomment;

 -- ADD COLUMNS TO WELIKIA_MW_ELEMENT

ALTER TABLE welikia_mw_element ADD COLUMN subset_rule text;

ALTER TABLE welikia_mw_element ADD COLUMN adjacency_rule integer;

-- IMPLEMENT RELATIONSHIP GROUP
/*
this statement will be written by Kim
*/

-- relationship type: value is a key to welikia_relationshiptype 
-- -- these changes can only be made after relationship group is added
-- ALTER TABLE welikia_mw_relationship ADD COLUMN relationship_type integer NOT NULL;
