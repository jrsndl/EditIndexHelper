# Edit Index Helper #

This tool can read Edit Index csv files from Davinci Resolve.
It can also find media files like Quicktime, and use ffprobe to read their metadata.

**Currently, the only functionality is to match media files by name to csv lines, and create EDL(s) from matches.**

## How to use it ##
Use command line arguments for basics:
-i to set root folder for Edit Index csv file(s).
-m to set root folder for searching media files.
-p to set custom path for prefs.json. See json _help items for more details
Without any arguments, the above -i and -m options will be read from prefs.json file ath the main script location.

## Functionality ##
(see prefs.json)

1. ### Search for edit index csv files
   1. You can set filter_include, filter_exclude, pattern to limit to certain file names only. Check_required_columns allows for forcing to only accept valid csv files.
   2. The CSV grouping allows to filter csv file names, and only read the file that is last in alphabetic sort. For example edit15_v01.csv, edit15_v02.csv can be set to only accept v02.
   3. The match skip options allows to add arbitrary number of filters to skip lines in csv file.
   
   
   * in prefs json, see **csv_columns**,  **search_csv**, **group_csv**:

      * **csv_columns - rename** Section renames arbitrary columns to internal names for important data
      * **csv_columns - required** Section has a list of columns required for the matching (see Matching)
      * **search_csv** Controls where to get the csv(s) from, and file filtering
      * **group_csv** Allows to group csv's, by regex, and sort the groups by regex. The **highest_only** option only takes into consideration the highest item in every sorted group.

2. ### Search for media files
   1. You can set filter_include, filter_exclude, pattern to limit to certain file names only.
   2. Media_meta options set paths to external tools for reading metadata.
   
   
   * in prefs json, see **search_media** and **media_meta**:

      * **search_media** controls where to get the media from, and file filtering
      * **media_meta** gives the path to ffprobe for extracting metadata from files

3. ### Matching media files to csv lines
   1. Csv matching column, csv_pattern, csv_repl allow to run regex search and replace on csv column of choice
   2. Csv matching media, media_pattern, media_repl allow to run regex search and replace on media file name
   3. If the regex result of 3.1 and 3.2 are matching, the media file and corresponding csv lines are merged
 
   
   * in prefs json, see **csv_match_skip** and **csv_matching** section:

      * **csv_match_skip** Section can have a list of filters that are run on every csv line. If one or more filters are true, the csv line is ignored. This is necessary for making sore all required data are present for matching.
      * **csv_matching** Section has text source plus regex for csv and media. If repl of csv and media regex matches, csv line is considered matching the media.

4. ### Exporting matching media files to EDL
   1. The EDL options control edl path and file name, plus frame rate options. Max reel option trims the reel to the number of characters specified.
   2. Edl reel options allow to run regex search and replace on source. Result is used for reel in the edl
   3. Edl clip options allow to run regex search and replace on source. Result is used for additional remark line
   4. Edl clip path options allow to run regex search and replace on source. Result is used for additional remark line.


   * in prefs json, see **edl**,  **edl_clip**,  **edl_clip_path**, **edl_reel** sections

      * **edl** Section controls name and location of output edl file
      * **edl_reel** outputs reel, or AX if regex fails
      * **edl_clip** Can output separate line for every matching media, ment to be used for AVID remarks
      * **edl_clip_path** Can output separate line for every matching media, ment to be used for AVID remarks

## Possible future updates can do ##
* Add ability to read metadata from EXR files
* GUI to interactively show the matching
* Find "longest take" from multiple edit index files for load / scan edl.
* Generating Countsheets
* Use edit index files together with marker exports from Davinci Resolve to assign vfx names to clips.  