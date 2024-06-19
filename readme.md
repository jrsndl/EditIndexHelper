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

1. Search for edit index csv files
   1. You can set filter_include, filter_exclude, pattern to limit to certain file names only. Check_required_columns allows for forcing to only accept valid csv files.
   2. The CSV grouping allows to filter csv file names, and only read the file that is last in alphabetic sort. For example edit15_v01.csv, edit15_v02.csv can be set to only accept v02.
   3. The match skip options allows to add arbitrary number of filters to skip lines in csv file.

2. Search for media files
   1. You can set filter_include, filter_exclude, pattern to limit to certain file names only.
   2. Media_meta options set paths to external tools for reading metadata.

3. Matching media files to csv lines
   1. Csv matching column, csv_pattern, csv_repl allow to run regex search and replace on csv column of choice
   2. Csv matching media, media_pattern, media_repl allow to run regex search and replace on media file name
   3. If the regex result of 3.1 and 3.2 are matching, the media file and corresponding csv lines are merged

4. Exporting matching media files to EDL
   1. The EDL options control edl path and file name, plus frame rate options. Max reel option trims the reel to the number of characters specified.
   2. Edl reel options allow to run regex search and replace on source. Result is used for reel in the edl
   3. Edl clip options allow to run regex search and replace on source. Result is used for additional remark line
   4. Edl clip path options allow to run regex search and replace on source. Result is used for additional remark line.


## Possible future updates can do ##
* Find "longest take" from multiple edit index files for load / scan edl.
* Use edit index files together with marker exports from Davinci Resolve to assign vfx names to clips.  