import os
from pprint import pprint
import logging
import mimetypes
from asset_folder_importer.asset_folder_sweeper.posix_get_mime import posix_get_mime

def find_files(cfg,db):
    #Step three. Find all relevant files and bung 'em in the database
    startpath = cfg.value('start_path',noraise=False)

    print "Running from '%s'" % startpath

    #mark a file as to be ignored, if any of these regexes match. This will prevent vsingester from importing it.
    pathShouldIgnore = [
        'Adobe Premiere Pro Preview Files',
        'System Volume Information',
        '\.TMP',
        '\/\.',  #should ignore a literal dot, but only if it follows a /
        '\.pk$',
        '\.PFL$',
        '\.PFR$',   #Cubase creates these filetypes
        '\.peak$',
        '_synctemp', #this is created by PluralEyes
    ]

    reShouldIgnore = []
    for expr in pathShouldIgnore:
        reShouldIgnore.append(re.compile(expr))

    #try:
    n=0
    for dirpath,dirnames,filenames in os.walk(startpath):
        #print dirpath
        #pprint(filenames)
        for name in filenames:
            if name.startswith('.'):
                continue
            shouldIgnore = False

            for regex in reShouldIgnore:
                if regex.search(dirpath) is not None:
                    shouldIgnore = True

            fullpath = os.path.join(dirpath,name)
            print fullpath
            try:
                statinfo = os.stat(fullpath)
                pprint(statinfo)
                mt = None
                try:
                    (mt, encoding) = mimetypes.guess_type(fullpath, strict=False)
                except Exception as e:
                    db.insert_sysparam("warning",e.message)

                if mt is None or mt == 'None':
                    mt = posix_get_mime(fullpath,db)

                db.upsert_file_record(dirpath,name,statinfo,mt,ignore=shouldIgnore)

            except UnicodeDecodeError as e:
                db.insert_sysparam("warning",str(e))
                logging.error(str(e))
            except OSError as e:
                db.insert_sysparam("warning",str(e))
                if e.errno == 2: #No Such File Or Directory
                    db.update_file_record_gone(dirpath,name)
            n+=1
            print "%d files...\r" %n

    return n