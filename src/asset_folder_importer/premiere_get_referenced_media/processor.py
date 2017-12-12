from asset_folder_importer.database import *
from asset_folder_importer.premiere_get_referenced_media.PremiereProject import PremiereProject
from asset_folder_importer.premiere_get_referenced_media.Exceptions import NoMediaError, InvalidDataError, NotInDatabaseError
from gnmvidispine.vs_collection import VSCollection
from gnmvidispine.vs_item import VSItem
from gnmvidispine.vidispine_api import *
import re
import logging

lg = logging.getLogger(__name__)


def id_from_filepath(filepath):
    """
    Retrieves the vidispine ID portion of a project name or None if none could be found
    :param filepath: filepath (full or relative) to check
    :return: String of the Vidispine ID or None of it could not be found
    """
    filename = os.path.basename(filepath)
    matches = re.search(u'^(.*)\.[^\.]+$', filename)
    if matches is not None:
        return matches.group(1)
    else:
        return None


def partial_path_match(source_path, target_path, limit_len):
    """
    Returns True if the source_path and target_path match up to a certain number of segments
    :param source_path: source path to check
    :param target_path: target path to check
    :param limit_len: number of path segments which must match
    :return: boolean
    """
    source_segments = source_path.split(os.path.sep)
    target_segments = target_path.split(os.path.sep)
    return do_partial_match(source_segments, target_segments, limit_len)


def do_partial_match(source_segments, target_segments, limit_len, start=0):
    """
    does the work for partial_path_match
    :param source_segments: list of path segments
    :param target_segments: list of path segments
    :param limit_len: number which must match
    :param start: number of segment at which to start (default=0)
    :return: boolean
    """
    if len(target_segments) > len(source_segments):
        n = len(source_segments)
        target_segments = target_segments[0:n]

    for n in range(start, limit_len):
        target_path = os.path.sep.join(target_segments[n:])
        source_path = os.path.sep.join(source_segments[n:])
        if source_path.endswith('/') and not target_path.endswith('/'):
            target_path += '/'
        lg.debug("comparing %s to %s" % (source_path, target_path))
        if source_path == target_path:
            return True


def find_vsstorage_for(filepath, vs_pathmap, limit_len):
    """
    Returns the relevant Vidispine storage for the given filepath or None if none could be found
    :param filepath: filepath to look up
    :param vs_pathmap: pathmap within which to go lookup. Get this from gnmvidispine.vs_storage.storage_path_map.
    :param limit_len: limit of matching path segments within which to consider the match
    :return: Vidispine ID of storage or None if none could be found
    """
    pathsegments = filter(None, filepath.split(os.path.sep))  #filter out null values

    for key, value in vs_pathmap.items():
        source_segments = filter(None, key.split(os.path.sep))
        #must start matching at 1, as the first part is /srv on the server and /Volumes on the client
        if do_partial_match(source_segments, pathsegments, limit_len, start=1):
            return value


def find_item_id_for_path(path, vs_pathmap):
    """
    Gets the Vidispine item ID for the given filepath. Raises VSNotFound if the path does not exist in VS
    :param path: path to check
    :return: item ID to which this file is attached.
    """
    storage = find_vsstorage_for(path, vs_pathmap,50)
    fileref = storage.fileForPath(path)
    return fileref.memberOfItem


def remove_path_chunks(filepath, to_remove):
    """
    Removes segments from the start of a file path. Raises AttributeError if the file path has less than to_remove segments
    :param filepath: file path to truncate
    :param to_remove: number of segments to remove
    :return: truncated file path
    """
    pathsegments = filepath.split(os.path.sep)
    return os.path.sep.join(pathsegments[to_remove:])


def process_premiere_fileref(filepath, server_path, vsproject, vs_pathmap=None, db=None, cfg=None):
    """
    process an individual file reference from a project
    :param filepath: file path to process
    :param server_path: file path as it is seen from the server
    :param project_database_id: database id of the premiere project
    :param db: assetimporter database object
    :param cfg: assetimporter configuration object
    :return: item reference or None
    """
    vsid = db.get_vidispine_id(server_path)
    # this call will return None if the file is not from an asset folder, e.g. newswire
    if vsid is None:
        vsid = find_item_id_for_path(server_path, vs_pathmap)
        if vsid is None:
            lg.error("File {0} is found by Vidispine but has not been imported yet".format(server_path))
            return None

    item = VSItem(host=cfg.value('vs_host'),port=cfg.value('vs_port'),user=cfg.value('vs_user'),passwd=cfg.value('vs_password'))
    item.populate(vsid,specificFields=['gnm_asset_category'])
    if item.get('gnm_asset_category') is not None:
        if item.get('gnm_asset_category').lower() == 'branding':
            lg.info("File %s is branding, not adding to project" % (filepath, ))
            return item
    else:
        lg.info("gnm_asset_category field empty so continuing")

    lg.debug("Got filepath %s" % filepath)
    fileid = db.fileId(server_path)
    if fileid:
        db.link_file_to_edit_project(fileid, vsproject.name)
        lg.debug("Linked file %s with id %s to project %s" % (filepath, fileid, vsproject.name))
    else:
        raise NotInDatabaseError
    return item


def process_premiere_project(filepath, raven_client, vs_pathmap=None, db=None, cfg=None):
    """
    Main function to process a Premiere project file
    :param filepath: file path to process
    :param db: asset importer database object
    :param cfg: asset importer configuration object
    :return:
    """
    lg.debug("---------------------------------")
    lg.info("Premiere project: %s" % filepath)

    collection_vsid = id_from_filepath(filepath)
    lg.info("Project's Vidispine ID: %s" % collection_vsid)
    vsproject = VSCollection(host=cfg.value('vs_host'), port=cfg.value('vs_port'), user=cfg.value('vs_user'),
                             passwd=cfg.value('vs_password'))
    vsproject.setName(collection_vsid)  #we don't need the actual metadata so don't bother getting it.

    pp = PremiereProject()
    try:
        pp.load(filepath)
    except Exception as e:
        lg.error("Unable to read '%s': %s" % (filepath,e.message))
        lg.error(traceback.format_exc())
        print "Unable to read '%s': %s" % (filepath,e.message)
        traceback.print_exc()
        raven_client.captureException()
        return (0,0,0)

    lg.debug("determining project details and updating database...")
    try:
        projectDetails = pp.getParticulars()

        project_id = db.upsert_edit_project(os.path.dirname(filepath), os.path.basename(filepath),
                                            projectDetails['uuid'], projectDetails['version'],
                                            desc="Adobe Premiere Project",
                                            opens_with="/Applications/Adobe Premiere Pro CC 2014/Adobe Premiere Pro CC 2014.app/Contents/MacOS/Adobe Premiere Pro CC 2014")
    except ValueError as e:
        project_id = db.log_project_issue(os.path.dirname(filepath), os.path.basename(filepath), problem="Invalid project file", detail="{0}: {1} {2}".format(e.__class__,str(e),traceback.format_exc()))
        lg.error("Unable to read project file '{0}' - {1}".format(filepath,str(e)))
        lg.error(traceback.format_exc())
        raven_client.captureException()
        return (0,0,0)
    except KeyError as e:
        project_id = db.log_project_issue(os.path.dirname(filepath), os.path.basename(filepath), problem="Invalid project file", detail="{0}: {1} {2}".format(e.__class__,str(e),traceback.format_exc()))
        db.insert_sysparam("warning","Unable to read project file '{0}' - {1}".format(filepath, str(e)))
        lg.error("Unable to read project file '{0}' - {1}".format(filepath,str(e)))
        lg.error(traceback.format_exc())
        raven_client.captureException()
        return (0,0,0)

    except InvalidDataError as e:
        db.insert_sysparam("warning","Corrupted project file: {0} {1}".format(filepath,unicode(e)))
        raven_client.captureException()
        return (0,0,0)

    total_files = 0
    no_vsitem = 0
    not_in_db = 0

    lg.debug("looking for referenced media....")
    for filepath in pp.getReferencedMedia():
        total_files += 1
        lg.debug("Looking up {0}".format(filepath))
        server_path = re.sub(u'^/Volumes', '/srv', filepath).encode('utf-8')
        try:
            item=process_premiere_fileref(filepath, server_path, project_id, vs_pathmap=vs_pathmap, db=db, cfg=cfg)
            #using this construct to avoid loading more data from VS than necessary.  We simply check whether the ID exists
            #in the parent collections list (cached on the item record) without lifting any more info out of VS
            if vsproject.name in map(lambda x: x.name,item.parent_collections(shouldPopulate=False)):
                lg.info("File %s is already in project %s" % (filepath,vsproject.name))
                continue

            vsproject.addToCollection(item=item)    #this call will apparently succeed if the item is already added to said collection, but it won't be added twice.
        except VSNotFound:
            lg.error("File {0} could not be found in either Vidispine or the asset importer database".format(server_path))
            if "Internet Downloads" in filepath:
                #note - this could raise a 400 exception IF there is a conflict with something else trying to add info to the same field
                vsproject.set_metadata({'gnm_project_invalid_media_paths': filepath}, mode="add")
            continue
        except NotInDatabaseError:
            not_in_db += 1
            lg.warning("File %s could not be found in the database" % filepath)
            continue
        except AlreadyLinkedError as e:
            lg.info("File %s with id %s is already linked to project %s" % (filepath, e.fileid, e.vsprojectid))
            continue

    lg.info(
        "Run complete. Out of a total of %d referenced files, %d did not have a Vidispine item and %d were not in the Asset Importer database" % (
            total_files, no_vsitem, not_in_db))
    return (total_files, no_vsitem, not_in_db)