#!/usr/bin/python

__author__ = 'Andy Gallagher <andy.gallagher@theguardian.com>'
__version__ = 'asset_folder_vsingester $Rev: 273 $ $LastChangedDate: 2015-07-28 22:00:39 +0100 (Tue, 28 Jul 2015) $'
__scriptname__ = 'asset_folder_vsingester'

#this also requires python-setuptools to be installed
from gnmvidispine.vs_storage import VSStorage
from asset_folder_importer.database import importer_db
from asset_folder_importer.config import configfile
from pprint import pprint
from optparse import OptionParser
import traceback
import os
import re
import time
import logging
from Queue import Queue
from asset_folder_importer.asset_folder_vsingester.exceptions import *
from asset_folder_importer.asset_folder_vsingester.importer_thread import *

MAXTHREADS = 8
#suid perl script so we don't need to run the whole shebang as root
PERMISSIONSCRIPT = "/usr/bin/asset_permissions.pl"
#set default encoding to utf-8 to prevent template errors
XML_CHECK_TIMEOUT = 60  #wait up to 60s for XML validation
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

# Configurable parameters
LOGFORMAT = '%(asctime)-15s - %(levelname)s - Thread %(thread)s - %(funcName)s: %(message)s'
main_log_level = logging.DEBUG
logfile = "/var/log/plutoscripts/asset_folder_vsingester.log"
graveyard_folder = "/var/log/plutoscripts/asset_folder_ingester_failed_xml"
#End configurable parameters


#This function is the main program, but is contained here to make it easier to catch exceptions
def innerMainFunc(cfg,db,limit):
    storageid=cfg.value('vs_masters_storage')
    logging.info("Connecting to storage with ID %s" % storageid)
    st=VSStorage(host=cfg.value('vs_host'),port=cfg.value('vs_port'),user=cfg.value('vs_user'),passwd=cfg.value('vs_password'))
    st.populate(storageid)

    possible_roots = []
    for uri in st.urisOfType('file',pathOnly=True,decode=True):
        logging.info("\tFile access at: %s" % uri)
        possible_roots.append(uri)

    #Step four. Find un-imported files and check to see if they are imported
    n=0
    found=0
    withItems=0
    imported=0

    threads = []
    input_queue = Queue()
    for i in range(0,MAXTHREADS):
        t = ImporterThread(input_queue,storageid,cfg,permission_script=PERMISSIONSCRIPT)
        t.start()
        threads.append(t)

    for fileref in db.filesForVSID(None, recent_first=True):
        if fileref['filename'].endswith('.cpr'): #don't import Cubase project files as items, they're already counted at the NAS
            db.update_file_ignore(fileref['id'],True)
            logging.info("Ignoring Cubase project %s/%s" % (fileref['filepath'],fileref['filename']))
            continue

        filepath = os.path.join(fileref['filepath'],fileref['filename'])
        # we need to remove the part of the filepath that corresponds to the storage path on the server
        for rootpath in possible_roots:
            filepath = re.sub(u'^{0}'.format(rootpath),'',filepath)

            n += 1

            input_queue.put([fileref,filepath,rootpath])
        if isinstance(limit, int):
            if n > limit:
                logging.warning("Reached requested limit of {0} items, stopping for now.".format(limit))
                break

    #tell the threads to terminate now
    for t in threads:
        input_queue.put((None,None,None))

    for t in threads:
        t.join()
        if t.isAlive():
            logging.warning("Thread {0} did not terminate properly".format(t.get_ident()))

        found += t.found
        withItems += t.withItems
        imported += t.imported

    db.insert_sysparam("without_vsid",n)
    db.insert_sysparam("found_in_vidispine",found)
    db.insert_sysparam("already_attached",withItems)
    logging.info("Info: Out of %d files in the database, %d were found in Vidispine and %d were attached to items" % (n,found,withItems))
    return (found,withItems,imported)

#START MAIN
#Step one. Commandline args.
parser = OptionParser()
parser.add_option("-c","--config", dest="configfile", help="import configuration from this file")
parser.add_option("-f","--force", dest="force", help="run even if it appears that another instance is already running")
parser.add_option("-l","--limit", dest="limit", help="stop after attempting to import this many files")
(options, args) = parser.parse_args()

#Step two. Read config
pprint(args)
pprint(options)

if options.configfile:
    cfg=configfile(options.configfile)
else:
    cfg=configfile("/etc/asset_folder_importer.cfg")

if logfile is not None:
    logging.basicConfig(filename=logfile, format=LOGFORMAT, level=main_log_level)
else:
    logging.basicConfig(format=LOGFORMAT, level=main_log_level)

logging.info("-----------------------------------------------------------\n\n")
logging.info("Connecting to database on %s" % cfg.value('database_host', noraise=True))

db = importer_db(__version__,hostname=cfg.value('database_host'),port=cfg.value('database_port'),username=cfg.value('database_user'),password=cfg.value('database_password'))

lastruntime = db.lastrun_endtime()
lastruntimestamp = 0

if lastruntime is None:
    logging.error("It appears that another instance of premiere_get_referenced_media is already running.")
    if not options.force:
        logging.error("Not continuing because --force is not specified on the commandline")
        db.end_run("already_running")
        exit(1)
    logging.warning("--force has been specified so running anyway")
else:
    lastruntimestamp = time.mktime(lastruntime.timetuple())

db.start_run(__scriptname__)

try:
    if options.limit is not None:
        db.insert_sysparam("limit",int(options.limit))
        db.commit()
        innerMainFunc(cfg,db,int(options.limit))
    else:
        innerMainFunc(cfg, db, None)
    db.insert_sysparam("exit","success")
except Exception as e:
    logging.error("An error occurred:")
    logging.error(str(e.__class__) + ": " + e.message)
    logging.error(traceback.format_exc())

    msgstring="{0}: {1}".format(str(e.__class__),e.message)
    db.cleanuperror()
    db.insert_sysparam("exit","error")
    db.insert_sysparam("errormsg",msgstring)
    db.insert_sysparam("stacktrace",traceback.format_exc())
    db.commit()
finally:
    db.end_run(status=None)
