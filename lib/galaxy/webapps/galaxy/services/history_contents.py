import datetime
import json
import logging
import os
import re
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Union,
)

from pydantic import (
    Extra,
    Field,
)
from typing_extensions import Literal

from galaxy import (
    exceptions,
    util
)
from galaxy.managers import (
    folders,
    hdas,
    hdcas,
    histories,
    history_contents
)
from galaxy.managers.base import (
    ModelSerializer,
)
from galaxy.managers.collections import DatasetCollectionManager
from galaxy.managers.collections_util import (
    api_payload_to_create_params,
    dictify_dataset_collection_instance,
)
from galaxy.managers.jobs import (
    fetch_job_states,
    summarize_jobs_to_dict,
)
from galaxy.managers.library_datasets import LibraryDatasetsManager
from galaxy.model import (
    History,
    HistoryDatasetAssociation,
    HistoryDatasetCollectionAssociation,
    LibraryDataset,
)
from galaxy.model.security import GalaxyRBACAgent
from galaxy.schema import (
    FilterQueryParams,
    SerializationParams,
)
from galaxy.schema.fields import (
    EncodedDatabaseIdField,
    OrderParamField,
)
from galaxy.schema.schema import (
    AnyHDA,
    AnyHistoryContentItem,
    AnyJobStateSummary,
    DatasetAssociationRoles,
    DeleteHDCAResult,
    HistoryContentSource,
    HistoryContentType,
    JobSourceType,
    Model,
    UpdateDatasetPermissionsPayload,
    UpdateHistoryContentsBatchPayload,
)
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.util.json import safe_dumps
from galaxy.util.zipstream import ZipstreamWrapper
from galaxy.webapps.galaxy.api.common import parse_serialization_params
from galaxy.webapps.galaxy.services.base import ServiceBase


log = logging.getLogger(__name__)

DatasetDetailsType = Union[Set[EncodedDatabaseIdField], Literal['all']]


class HistoryContentsFilterQueryParams(FilterQueryParams):
    order: Optional[str] = OrderParamField(default_order="hid-asc")


HistoryContentFilter = List[Any]  # Lists with [attribute:str, operator:str, value:Any]
HistoryContentsFilterList = List[HistoryContentFilter]


class HistoryContentsIndexLegacyParamsBase(Model):
    deleted: Optional[bool] = Field(
        default=None,
        title="Deleted",
        description="Whether to return deleted or undeleted datasets only. Leave unset for both.",
    )
    visible: Optional[bool] = Field(
        default=None,
        title="Visible",
        description="Whether to return visible or hidden datasets only. Leave unset for both.",
    )


class HistoryContentsIndexLegacyParams(HistoryContentsIndexLegacyParamsBase):
    v: Optional[str] = Field(  # Should this be deprecated at some point and directly use the latest version by default?
        default=None,
        title="Version",
        description="Only `dev` value is allowed. Set it to use the latest version of this endpoint.",
    )
    ids: Optional[str] = Field(
        default=None,
        title="IDs",
        description=(
            "A comma separated list of encoded `HDA` IDs. If this list is provided, only information about the "
            "specific datasets will be provided. Also, setting this value will set `dataset_details` to `all`."
        ),
    )
    types: Optional[Union[List[HistoryContentType], str]] = Field(
        default=None,
        alias="type",  # Legacy alias
        title="Types",
        description=(
            "A list or comma separated list of kinds of contents to return "
            "(currently just `dataset` and `dataset_collection` are available)."
        ),
    )
    dataset_details: Optional[str] = Field(
        default=None,
        alias="details",  # Legacy alias
        title="Dataset Details",
        description=(
            "A comma separated list of encoded dataset IDs that will return additional (full) details "
            "instead of the *summary* default information."
        ),
    )


class ParsedHistoryContentsLegacyParams(HistoryContentsIndexLegacyParamsBase):
    ids: Optional[List[int]] = Field(
        default=None,
        title="IDs",
        description="A list of (decoded) `HDA` IDs to return detailed information. Only these items will be returned.",
    )
    types: List[HistoryContentType] = Field(
        default=[],
        title="Types",
        description="A list with the types of contents to return.",
    )
    dataset_details: Optional[DatasetDetailsType] = Field(
        default=None,
        title="Dataset Details",
        description=(
            "A set of encoded IDs for the datasets that will be returned with detailed "
            "information or the value `all` to return (full) details for all datasets."
        ),
    )


class CreateHistoryContentPayloadBase(Model):
    type: Optional[HistoryContentType] = Field(
        HistoryContentType.dataset,
        title="Type",
        description="The type of content to be created in the history.",
    )


class CreateHistoryContentPayloadFromCopy(CreateHistoryContentPayloadBase):
    source: Optional[HistoryContentSource] = Field(
        None,
        title="Source",
        description="The source of the content. Can be other history element to be copied or library elements.",
    )
    content: Optional[EncodedDatabaseIdField] = Field(
        None,
        title="Content",
        description=(
            "Depending on the `source` it can be:\n"
            "- The encoded id from the library dataset\n"
            "- The encoded id from the library folder\n"
            "- The encoded id from the HDA\n"
            "- The encoded id from the HDCA\n"
        ),
    )


class CreateHistoryContentPayloadFromCollection(CreateHistoryContentPayloadFromCopy):
    dbkey: Optional[str] = Field(
        default=None,
        title="DBKey",
        description="TODO",
    )
    copy_elements: Optional[bool] = Field(
        default=False,
        title="Copy Elements",
        description=(
            "If the source is a collection, whether to copy child HDAs into the target "
            "history as well, defaults to False but this is less than ideal and may "
            "be changed in future releases."
        ),
    )


class CreateHistoryContentPayload(CreateHistoryContentPayloadFromCollection):
    class Config:
        extra = Extra.allow


class HistoriesContentsService(ServiceBase):
    """Common interface/service logic for interactions with histories contents in the context of the API.

    Provides the logic of the actions invoked by API controllers and uses type definitions
    and pydantic models to declare its parameters and return types.
    """

    def __init__(
        self,
        security: IdEncodingHelper,
        history_manager: histories.HistoryManager,
        history_contents_manager: history_contents.HistoryContentsManager,
        hda_manager: hdas.HDAManager,
        dataset_collection_manager: DatasetCollectionManager,
        ldda_manager: LibraryDatasetsManager,
        folder_manager: folders.FolderManager,
        hda_serializer: hdas.HDASerializer,
        hda_deserializer: hdas.HDADeserializer,
        hdca_serializer: hdcas.HDCASerializer,
        history_contents_filters: history_contents.HistoryContentsFilters,
    ):
        super().__init__(security)
        self.history_manager = history_manager
        self.history_contents_manager = history_contents_manager
        self.hda_manager = hda_manager
        self.dataset_collection_manager = dataset_collection_manager
        self.ldda_manager = ldda_manager
        self.folder_manager = folder_manager
        self.hda_serializer = hda_serializer
        self.hda_deserializer = hda_deserializer
        self.hdca_serializer = hdca_serializer
        self.history_contents_filters = history_contents_filters

    def index(
        self,
        trans,
        history_id: EncodedDatabaseIdField,
        legacy_params: HistoryContentsIndexLegacyParams,
        serialization_params: SerializationParams,
        filter_query_params: HistoryContentsFilterQueryParams,
    ) -> List[AnyHistoryContentItem]:
        """
        Return a list of contents (HDAs and HDCAs) for the history with the given ``id``

        .. note:: Anonymous users are allowed to get their current history contents
        """
        if legacy_params.v == 'dev':
            return self.__index_v2(
                trans, history_id, legacy_params, serialization_params, filter_query_params
            )
        return self.__index_legacy(trans, history_id, legacy_params)

    def show(
        self,
        trans,
        id: EncodedDatabaseIdField,
        serialization_params: SerializationParams,
        contents_type: HistoryContentType = HistoryContentType.dataset,
        fuzzy_count: Optional[int] = None,
    ) -> AnyHistoryContentItem:
        """
        Return detailed information about an HDA or HDCA within a history

        .. note:: Anonymous users are allowed to get their current history contents

        :param  id:                         the encoded id of the HDA or HDCA to return
        :param  contents_type:              'dataset' or 'dataset_collection'
        :param  serialization_params.view:  if fetching a dataset collection - the view style of
                                            the dataset collection to produce.
                                            'collection' returns no element information, 'element'
                                            returns detailed element information for all datasets,
                                            'element-reference' returns a minimal set of information
                                            about datasets (for instance id, type, and state but not
                                            metadata, peek, info, or name). The default is 'element'.
        :param fuzzy_count: this value can be used to broadly restrict the magnitude
                            of the number of elements returned via the API for large
                            collections. The number of actual elements returned may
                            be "a bit" more than this number or "a lot" less - varying
                            on the depth of nesting, balance of nesting at each level,
                            and size of target collection. The consumer of this API should
                            not expect a stable number or pre-calculable number of
                            elements to be produced given this parameter - the only
                            promise is that this API will not respond with an order
                            of magnitude more elements estimated with this value.
                            The UI uses this parameter to fetch a "balanced" concept of
                            the "start" of large collections at every depth of the
                            collection.

        :returns:   dictionary containing detailed HDA or HDCA information
        """
        if contents_type == HistoryContentType.dataset:
            return self.__show_dataset(trans, id, serialization_params)
        elif contents_type == HistoryContentType.dataset_collection:
            return self.__show_dataset_collection(trans, id, serialization_params, fuzzy_count)
        raise exceptions.UnknownContentsType(f'Unknown contents type: {contents_type}')

    def index_jobs_summary(
        self, trans,
        ids: List[EncodedDatabaseIdField],
        types: List[JobSourceType],
    ) -> List[AnyJobStateSummary]:
        """
        Return job state summary info for jobs, implicit groups jobs for collections or workflow invocations

        Warning: We allow anyone to fetch job state information about any object they
        can guess an encoded ID for - it isn't considered protected data. This keeps
        polling IDs as part of state calculation for large histories and collections as
        efficient as possible.

        :param  ids:    the encoded ids of job summary objects to return - if ids
                        is specified types must also be specified and have same length.
        :param  types:  type of object represented by elements in the ids array - any of
                        Job, ImplicitCollectionJob, or WorkflowInvocation.

        :returns:   an array of job summary object dictionaries.
        """
        if len(ids) != len(types):
            raise exceptions.RequestParameterInvalidException(
                f"The number of ids ({len(ids)}) and types ({len(types)}) must match."
            )
        decoded_ids = self.decode_ids(ids)
        return [
            self.encode_all_ids(job_state)
            for job_state in fetch_job_states(trans.sa_session, decoded_ids, types)
        ]

    def show_jobs_summary(
        self, trans,
        id: EncodedDatabaseIdField,
        contents_type: HistoryContentType = HistoryContentType.dataset,
    ) -> AnyJobStateSummary:
        """
        Return detailed information about an HDA or HDCAs jobs

        Warning: We allow anyone to fetch job state information about any object they
        can guess an encoded ID for - it isn't considered protected data. This keeps
        polling IDs as part of state calculation for large histories and collections as
        efficient as possible.

        :param  id:             the encoded id of the HDA or HDCA to return
        :param  contents_type:  'dataset' or 'dataset_collection'

        :returns:   dictionary containing jobs summary object
        """
        # At most one of job or implicit_collection_jobs should be found.
        job = None
        implicit_collection_jobs = None
        if contents_type == HistoryContentType.dataset:
            hda = self.hda_manager.get_accessible(self.decode_id(id), trans.user)
            job = hda.creating_job
        elif contents_type == HistoryContentType.dataset_collection:
            dataset_collection_instance = self.__get_accessible_collection(trans, id)
            job_source_type = dataset_collection_instance.job_source_type
            if job_source_type == JobSourceType.Job:
                job = dataset_collection_instance.job
            elif job_source_type == JobSourceType.ImplicitCollectionJobs:
                implicit_collection_jobs = dataset_collection_instance.implicit_collection_jobs

        assert job is None or implicit_collection_jobs is None
        return self.encode_all_ids(summarize_jobs_to_dict(trans.sa_session, job or implicit_collection_jobs))

    def download_dataset_collection(self, trans, id: EncodedDatabaseIdField):
        """
        Download the content of a HistoryDatasetCollection as a tgz archive
        while maintaining approximate collection structure.

        :param id: encoded HistoryDatasetCollectionAssociation (HDCA) id
        """
        try:
            dataset_collection_instance = self.__get_accessible_collection(trans, id)
            return self.__stream_dataset_collection(trans, dataset_collection_instance)
        except Exception as e:
            log.exception("Error in API while creating dataset collection archive")
            trans.response.status = 500
            return {'error': util.unicodify(e)}

    def __stream_dataset_collection(self, trans, dataset_collection_instance):
        archive = hdcas.stream_dataset_collection(dataset_collection_instance=dataset_collection_instance, upstream_mod_zip=trans.app.config.upstream_mod_zip)
        trans.response.headers.update(archive.get_headers())
        return archive.response()

    def create(
        self, trans,
        history_id: EncodedDatabaseIdField,
        payload: CreateHistoryContentPayload,
        serialization_params: SerializationParams,
    ) -> AnyHistoryContentItem:
        """
        Create a new HDA or HDCA.

        ..note:
            Currently, a user can only copy an HDA from a history that the user owns.
        """
        history = self.history_manager.get_owned(
            self.decode_id(history_id), trans.user, current_history=trans.history
        )

        type = payload.type
        if type == HistoryContentType.dataset:
            source = payload.source
            if source == HistoryContentSource.library_folder:
                return self.__create_datasets_from_library_folder(trans, history, payload, serialization_params)
            else:
                return self.__create_dataset(trans, history, payload, serialization_params)
        elif type == HistoryContentType.dataset_collection:
            return self.__create_dataset_collection(trans, history, payload, serialization_params)
        raise exceptions.UnknownContentsType(f'Unknown contents type: {payload.type}')

    def update_permissions(
        self, trans,
        history_content_id: EncodedDatabaseIdField,
        payload: UpdateDatasetPermissionsPayload,
    ) -> DatasetAssociationRoles:
        """
        Set permissions of the given dataset to the given role ids.

        :param   payload: dictionary structure containing:
            :param  action:     (required) describes what action should be performed
                                available actions: make_private, remove_restrictions, set_permissions
            :type   action:     string
            :param  access_ids[]:      list of Role.id defining roles that should have access permission on the dataset
            :type   access_ids[]:      string or list
            :param  manage_ids[]:      list of Role.id defining roles that should have manage permission on the dataset
            :type   manage_ids[]:      string or list
            :param  modify_ids[]:      list of Role.id defining roles that should have modify permission on the library dataset item
            :type   modify_ids[]:      string or list

        :raises: RequestParameterInvalidException, ObjectNotFound, InsufficientPermissionsException, InternalServerError
                    RequestParameterMissingException
        """
        payload_dict = payload.dict(by_alias=True)
        hda = self.hda_manager.get_owned(self.decode_id(history_content_id), trans.user, current_history=trans.history, trans=trans)
        assert hda is not None
        self.hda_manager.update_permissions(trans, hda, **payload_dict)
        roles = self.hda_manager.serialize_dataset_association_roles(trans, hda)
        return DatasetAssociationRoles.parse_obj(roles)

    def update(
        self, trans,
        history_id: EncodedDatabaseIdField,
        id: EncodedDatabaseIdField,
        payload: Dict[str, Any],
        serialization_params: SerializationParams,
        contents_type: HistoryContentType = HistoryContentType.dataset,
    ):
        """
        Updates the values for the history content item with the given ``id``

        :param  history_id: encoded id string of the items's History
        :param  id:         the encoded id of the history item to update
        :param  payload:    a dictionary containing any or all the fields for
                            HDA or HDCA with values different than the defaults

        :returns:   an error object if an error occurred or a dictionary containing
                    any values that were different from the original and, therefore, updated
        """
        if contents_type == HistoryContentType.dataset:
            return self.__update_dataset(trans, history_id, id, payload, serialization_params)
        elif contents_type == HistoryContentType.dataset_collection:
            return self.__update_dataset_collection(trans, id, payload)
        else:
            raise exceptions.UnknownContentsType(f'Unknown contents type: {contents_type}')

    def update_batch(
        self, trans,
        history_id: EncodedDatabaseIdField,
        payload: UpdateHistoryContentsBatchPayload,
        serialization_params: SerializationParams,
    ):
        """
        PUT /api/histories/{history_id}/contents

        :type   history_id: str
        :param  history_id: encoded id string of the history containing supplied items
        :type   id:         str
        :param  id:         the encoded id of the history to update
        :type   payload:    dict
        :param  payload:    a dictionary containing any or all the

        :rtype:     dict
        :returns:   an error object if an error occurred or a dictionary containing
                    any values that were different from the original and, therefore, updated
        """
        history = self.history_manager.get_owned(
            self.decode_id(history_id), trans.user, current_history=trans.history
        )
        items = payload.items
        hda_ids = []
        hdca_ids = []
        for item in items:
            contents_type = item.history_content_type
            if contents_type == HistoryContentType.dataset:
                decoded_id = self.decode_id(item.id)
                hda_ids.append(decoded_id)
            else:
                hdca_ids.append(item.id)
        payload_dict = payload.dict(exclude_unset=True)
        hdas = self.__datasets_for_update(trans, history, hda_ids, payload_dict)
        rval = []
        for hda in hdas:
            self.__deserialize_dataset(trans, hda, payload_dict)
            serialization_params.default_view = "summary"
            rval.append(self.hda_serializer.serialize_to_view(
                hda, user=trans.user, trans=trans, **serialization_params.dict()
            ))
        for hdca_id in hdca_ids:
            self.__update_dataset_collection(trans, hdca_id, payload.dict(exclude_defaults=True))
            dataset_collection_instance = self.__get_accessible_collection(trans, hdca_id)
            rval.append(self.__collection_dict(trans, dataset_collection_instance, view="summary"))
        return rval

    def validate(
        self, trans,
        history_id: EncodedDatabaseIdField,
        history_content_id: EncodedDatabaseIdField
    ):
        """
        Updates the values for the history content item with the given ``id``

        :type   history_id: str
        :param  history_id: encoded id string of the items's History
        :type   id:         str
        :param  id:         the encoded id of the history item to validate

        :rtype:     dict
        :returns:   TODO
        """
        decoded_id = self.decode_id(history_content_id)
        history = self.history_manager.get_owned(
            self.decode_id(history_id), trans.user, current_history=trans.history
        )
        hda = self.hda_manager.get_owned_ids([decoded_id], history=history)[0]
        if hda:
            self.hda_manager.set_metadata(trans, hda, overwrite=True, validate=True)
        return {}

    def delete(
        self, trans,
        id,
        serialization_params: SerializationParams,
        contents_type: HistoryContentType = HistoryContentType.dataset,
        purge: bool = False,
        recursive: bool = False,
    ) -> Union[AnyHDA, DeleteHDCAResult]:
        """
        Delete the history content with the given ``id`` and specified type (defaults to dataset)

        .. note:: Currently does not stop any active jobs for which this dataset is an output.

        :param  id:     the encoded id of the history item to delete
        :type   recursive:  bool
        :param  recursive:  if True, and deleted an HDCA also delete containing HDAs
        :type   purge:  bool
        :param  purge:  if True, purge the target HDA or child HDAs of the target HDCA

        :rtype:     dict
        :returns:   an error object if an error occurred or a dictionary containing:
            * id:         the encoded id of the history,
            * deleted:    if the history content was marked as deleted,
            * purged:     if the history content was purged
        """
        if contents_type == HistoryContentType.dataset:
            return self.__delete_dataset(trans, id, purge, serialization_params)
        elif contents_type == HistoryContentType.dataset_collection:
            self.dataset_collection_manager.delete(trans, "history", id, recursive=recursive, purge=purge)
            return DeleteHDCAResult(id=id, deleted=True)
        else:
            raise exceptions.UnknownContentsType(f'Unknown contents type: {contents_type}')

    def archive(
        self, trans,
        history_id: EncodedDatabaseIdField,
        filter_query_params: HistoryContentsFilterQueryParams,
        filename: str = '',
        dry_run: bool = True,
    ):
        """
        Build and return a compressed archive of the selected history contents

        :type   filename:  string
        :param  filename:  (optional) archive name (defaults to history name)
        :type   dry_run:   boolean
        :param  dry_run:   (optional) if True, return the archive and file paths only
                           as json and not an archive file

        :returns:   archive file for download or json in `dry run` mode
        """
        # roughly from: http://stackoverflow.com/a/31976060 (windows, linux)
        invalid_filename_char_regex = re.compile(r'[:<>|\\\/\?\* "]')
        # path format string - dot separator between id and name
        id_name_format = '{}.{}'

        def name_to_filename(name, max_length=150, replace_with='_'):
            # TODO: seems like shortening unicode with [:] would cause unpredictable display strings
            return invalid_filename_char_regex.sub(replace_with, name)[0:max_length]

        # given a set of parents for a dataset (HDCAs, DC, DCEs, etc.) - build a directory structure that
        # (roughly) recreates the nesting in the contents using the parent names and ids
        def build_path_from_parents(parents):
            parent_names = []
            for parent in parents:
                # an HDCA
                if hasattr(parent, 'hid'):
                    name = name_to_filename(parent.name)
                    parent_names.append(id_name_format.format(parent.hid, name))
                # a DCE
                elif hasattr(parent, 'element_index'):
                    name = name_to_filename(parent.element_identifier)
                    parent_names.append(id_name_format.format(parent.element_index, name))
            # NOTE: DCs are skipped and use the wrapping DCE info instead
            return parent_names

        # get the history used for the contents query and check for accessibility
        history = self.history_manager.get_accessible(trans.security.decode_id(history_id), trans.user)
        archive_base_name = filename or name_to_filename(history.name)

        # this is the fn applied to each dataset contained in the query
        paths_and_files = []

        def build_archive_files_and_paths(content, *parents):
            archive_path = archive_base_name
            if not self.hda_manager.is_accessible(content, trans.user):
                # if the underlying dataset is not accessible, skip it silently
                return

            content_container_id = content.hid
            content_name = name_to_filename(content.name)
            if parents:
                if hasattr(parents[0], 'element_index'):
                    # if content is directly wrapped in a DCE, strip it from parents (and the resulting path)
                    # and instead replace the content id and name with the DCE index and identifier
                    parent_dce, parents = parents[0], parents[1:]
                    content_container_id = parent_dce.element_index
                    content_name = name_to_filename(parent_dce.element_identifier)
                # reverse for path from parents: oldest parent first
                archive_path = os.path.join(archive_path, *build_path_from_parents(parents)[::-1])
                # TODO: this is brute force - building the path each time instead of re-using it
                # possibly cache

            # add the name as the last element in the archive path
            content_id_and_name = id_name_format.format(content_container_id, content_name)
            archive_path = os.path.join(archive_path, content_id_and_name)

            # ---- for composite files, we use id and name for a directory and, inside that, ...
            if self.hda_manager.is_composite(content):
                # ...save the 'main' composite file (gen. html)
                paths_and_files.append((content.file_name, os.path.join(archive_path, f"{content.name}.html")))
                for extra_file in self.hda_manager.extra_files(content):
                    extra_file_basename = os.path.basename(extra_file)
                    archive_extra_file_path = os.path.join(archive_path, extra_file_basename)
                    # ...and one for each file in the composite
                    paths_and_files.append((extra_file, archive_extra_file_path))

            # ---- for single files, we add the true extension to id and name and store that single filename
            else:
                # some dataset names can contain their original file extensions, don't repeat
                if not archive_path.endswith(f".{content.extension}"):
                    archive_path += f".{content.extension}"
                paths_and_files.append((content.file_name, archive_path))

        # filter the contents that contain datasets using any filters possible from index above and map the datasets
        filters = self.history_contents_filters.parse_query_filters(filter_query_params)
        self.history_contents_manager.map_datasets(history, build_archive_files_and_paths, filters=filters)

        # if dry_run, return the structure as json for debugging
        if dry_run:
            trans.response.headers['Content-Type'] = 'application/json'
            return safe_dumps(paths_and_files)

        # create the archive, add the dataset files, then stream the archive as a download
        archive = ZipstreamWrapper(
            archive_name=archive_base_name,
            upstream_mod_zip=trans.app.config.upstream_mod_zip,
            upstream_gzip=trans.app.config.upstream_gzip,
        )
        for file_path, archive_path in paths_and_files:
            archive.write(file_path, archive_path)

        trans.response.headers.update(archive.get_headers())
        return archive.response()

    def contents_near(
        self, trans,
        history_id: EncodedDatabaseIdField,
        serialization_params: SerializationParams,
        filter_params: HistoryContentsFilterList,
        hid: int,
        limit: int,
        since: Optional[datetime.datetime] = None,
    ):
        """
        This endpoint provides random access to a large history without having
        to know exactly how many pages are in the final query. Pick a target HID
        and filters, and the endpoint will get LIMIT counts above and below that
        target regardless of how many gaps may exist in the HID due to
        filtering.

        It does 2 queries, one up and one down from the target hid with a
        result size of limit. Additional counts for total matches of both seeks
        provided in the http headers.

        I've also abandoned the wierd q/qv syntax.
        """
        history: History = self.history_manager.get_accessible(
            self.decode_id(history_id), trans.user, current_history=trans.history
        )

        # while polling, check to see if the history has changed
        # if it hasn't then we can short-circuit the poll request
        if since:
            # sqlalchemy DateTime columns are not timezone aware, but `since` may be a timezone aware
            # DateTime object if a timezone offset is provided (https://github.com/samuelcolvin/pydantic/blob/5ccbdcb5904f35834300b01432a665c75dc02296/pydantic/datetime_parse.py#L179).
            # If a timezone is provided (since.tzinfo is not None) we convert to UTC and remove tzinfo so that comparison with history.update_time is correct.
            since = since if since.tzinfo is None else since.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            if history.update_time <= since:
                trans.response.status = 204
                return

        # SEEK UP, contents > hid
        up_params = filter_params + self._hid_greater_than(hid)
        up_order = 'hid-asc'
        contents_up, up_count = self._seek(history, up_params, up_order, limit, serialization_params)

        # SEEK DOWN, contents <= hid
        down_params = filter_params + self._hid_less_or_equal_than(hid)
        down_order = 'hid-dsc'
        contents_down, down_count = self._seek(history, down_params, down_order, limit, serialization_params)

        # min/max hid values
        min_hid, max_hid = self._get_filtered_extrema(history, filter_params)

        # results
        up = self._expand_contents(trans, contents_up, serialization_params)
        up.reverse()
        down = self._expand_contents(trans, contents_down, serialization_params)
        contents = up + down

        # Put stats in http headers
        trans.response.headers['matches_up'] = len(contents_up)
        trans.response.headers['matches_down'] = len(contents_down)
        trans.response.headers['total_matches_up'] = up_count
        trans.response.headers['total_matches_down'] = down_count
        trans.response.headers['max_hid'] = max_hid
        trans.response.headers['min_hid'] = min_hid
        trans.response.headers['history_size'] = str(history.disk_size)
        trans.response.headers['history_empty'] = json.dumps(history.empty)  # convert to proper bool

        return json.dumps(contents)

    def _hid_greater_than(self, hid: int) -> HistoryContentsFilterList:
        return [["hid", "gt", hid]]

    def _hid_less_or_equal_than(self, hid: int) -> HistoryContentsFilterList:
        return [["hid", "le", hid]]

    # Perform content query and matching count
    def _seek(self, history, filter_params, order_by_string, limit, serialization_params):
        filters = self.history_contents_filters.parse_filters(filter_params)
        order_by = self.build_order_by(self.history_contents_manager, order_by_string)

        # actual contents
        contents = self.history_contents_manager.contents(history,
            filters=filters,
            limit=limit,
            offset=0,
            order_by=order_by,
            serialization_params=serialization_params)

        # count of same query
        count_filter_params = [f for f in filter_params if f[0] != 'update_time']
        count_filters = self.history_contents_filters.parse_filters(count_filter_params)
        contents_count = self.history_contents_manager.contents_count(history, count_filters)

        return contents, contents_count

    # Adds subquery details to initial contents results, perhaps better realized
    # as a proc or view.
    def _expand_contents(self, trans, contents, serialization_params: SerializationParams):
        rval = []
        for content in contents:
            if isinstance(content, HistoryDatasetAssociation):
                dataset = self.hda_serializer.serialize_to_view(content,
                    user=trans.user, trans=trans, **serialization_params.dict())
                rval.append(dataset)
            elif isinstance(content, HistoryDatasetCollectionAssociation):
                collection = self.hdca_serializer.serialize_to_view(content,
                    user=trans.user, trans=trans, **serialization_params.dict())
                rval.append(collection)
        return rval

    def _get_filtered_extrema(self, history, filter_params):
        extrema_params = parse_serialization_params(keys='hid', default_view='summary')
        extrema_filter_params = [f for f in filter_params if f[0] != 'update_time']
        extrema_filters = self.history_contents_filters.parse_filters(extrema_filter_params)

        order_by_dsc = self.build_order_by(self.history_contents_manager, 'hid-dsc')
        order_by_asc = self.build_order_by(self.history_contents_manager, 'hid-asc')

        max_row_result = self.history_contents_manager.contents(history,
            limit=1,
            filters=extrema_filters,
            order_by=order_by_dsc,
            serialization_params=extrema_params)
        max_row = max_row_result.pop() if len(max_row_result) else None

        min_row_result = self.history_contents_manager.contents(history,
            limit=1,
            filters=extrema_filters,
            order_by=order_by_asc,
            serialization_params=extrema_params)
        min_row = min_row_result.pop() if len(min_row_result) else None

        max_hid = max_row.hid if max_row else None
        min_hid = min_row.hid if min_row else None

        return min_hid, max_hid

    def __delete_dataset(
        self, trans,
        id: EncodedDatabaseIdField,
        purge: bool,
        serialization_params: SerializationParams
    ):
        hda = self.hda_manager.get_owned(self.decode_id(id), trans.user, current_history=trans.history)
        self.hda_manager.error_if_uploading(hda)

        if purge:
            self.hda_manager.purge(hda)
        else:
            self.hda_manager.delete(hda)
        serialization_params.default_view = 'detailed'
        return self.hda_serializer.serialize_to_view(
            hda, user=trans.user, trans=trans, **serialization_params.dict()
        )

    def __update_dataset_collection(self, trans, id: EncodedDatabaseIdField, payload: Dict[str, Any]):
        return self.dataset_collection_manager.update(trans, "history", id, payload)

    def __update_dataset(
        self, trans,
        history_id: EncodedDatabaseIdField,
        id: EncodedDatabaseIdField,
        payload: Dict[str, Any],
        serialization_params: SerializationParams
    ):
        # anon user: ensure that history ids match up and the history is the current,
        #   check for uploading, and use only the subset of attribute keys manipulatable by anon users
        decoded_id = self.decode_id(id)
        history = self.history_manager.get_owned(
            self.decode_id(history_id), trans.user, current_history=trans.history
        )
        hda = self.__datasets_for_update(trans, history, [decoded_id], payload)[0]
        if hda:
            self.__deserialize_dataset(trans, hda, payload)
            serialization_params.default_view = 'detailed'
            return self.hda_serializer.serialize_to_view(
                hda, user=trans.user, trans=trans, **serialization_params.dict()
            )
        return {}

    def __datasets_for_update(
        self, trans,
        history: History,
        hda_ids: List[int],
        payload: Dict[str, Any]
    ):
        anonymous_user = not trans.user_is_admin and trans.user is None
        if anonymous_user:
            anon_allowed_payload = {}
            if 'deleted' in payload:
                anon_allowed_payload['deleted'] = payload['deleted']
            if 'visible' in payload:
                anon_allowed_payload['visible'] = payload['visible']
            payload = anon_allowed_payload

        hdas = self.hda_manager.get_owned_ids(hda_ids, history=history)

        # only check_state if not deleting, otherwise cannot delete uploading files
        check_state = not payload.get('deleted', False)
        if check_state:
            for hda in hdas:
                hda = self.hda_manager.error_if_uploading(hda)

        return hdas

    def __deserialize_dataset(self, trans, hda, payload: Dict[str, Any]):
        self.hda_deserializer.deserialize(hda, payload, user=trans.user, trans=trans)
        # TODO: this should be an effect of deleting the hda
        if payload.get('deleted', False):
            self.hda_manager.stop_creating_job(hda)

    def __index_legacy(
        self, trans,
        history_id: EncodedDatabaseIdField,
        legacy_params: HistoryContentsIndexLegacyParams,
    ) -> List[AnyHistoryContentItem]:
        """Legacy implementation of the `index` action."""
        history = self._get_history(trans, history_id)
        parsed_legacy_params = self._parse_legacy_contents_params(legacy_params)
        contents = history.contents_iter(**parsed_legacy_params)
        return [
            self._serialize_legacy_content_item(trans, content, parsed_legacy_params.get("dataset_details"))
            for content in contents
        ]

    def __index_v2(
        self,
        trans,
        history_id: EncodedDatabaseIdField,
        legacy_params: HistoryContentsIndexLegacyParams,
        serialization_params: SerializationParams,
        filter_query_params: HistoryContentsFilterQueryParams,
    ) -> List[AnyHistoryContentItem]:
        """
        Latests implementation of the `index` action.
        Allows additional filtering of contents and custom serialization.
        """
        history = self._get_history(trans, history_id)
        filters = self.history_contents_filters.parse_query_filters(filter_query_params)
        order_by = self.build_order_by(self.history_contents_manager, filter_query_params.order)

        # TODO: > 16.04: remove these
        # TODO: remove 'dataset_details' and the following section when the UI doesn't need it
        parsed_legacy_params = self._parse_legacy_contents_params(legacy_params)
        contents = self.history_contents_manager.contents(
            history,
            filters=filters,
            limit=filter_query_params.limit,
            offset=filter_query_params.offset,
            order_by=order_by,
            serialization_params=serialization_params
        )
        return [
            self._serialize_content_item(
                trans, content,
                dataset_details=parsed_legacy_params.get("dataset_details"),
                serialization_params=serialization_params,
            )
            for content in contents
        ]

    def _serialize_legacy_content_item(
        self,
        trans,
        content,
        dataset_details: Optional[DatasetDetailsType] = None,
    ):
        encoded_content_id = self.encode_id(content.id)
        detailed = dataset_details and (dataset_details == 'all' or (encoded_content_id in dataset_details))
        if isinstance(content, HistoryDatasetAssociation):
            view = 'detailed' if detailed else 'summary'
            return self.hda_serializer.serialize_to_view(content, view=view, user=trans.user, trans=trans)
        elif isinstance(content, HistoryDatasetCollectionAssociation):
            view = 'element' if detailed else 'collection'
            return self.__collection_dict(trans, content, view=view)

    def _serialize_content_item(
            self,
            trans,
            content,
            dataset_details: Optional[DatasetDetailsType],
            serialization_params: SerializationParams,
            default_view: str = "summary",
    ):
        """
        Returns a dictionary with the appropriate values depending on the
        serialization parameters provided.
        """
        serialization_params_dict = serialization_params.dict()
        view = serialization_params_dict.pop("view", default_view) or default_view

        serializer: Optional[ModelSerializer] = None
        if isinstance(content, HistoryDatasetAssociation):
            serializer = self.hda_serializer
            if dataset_details and (dataset_details == 'all' or self.encode_id(content.id) in dataset_details):
                view = "detailed"
        elif isinstance(content, HistoryDatasetCollectionAssociation):
            serializer = self.hdca_serializer

        if serializer is None:
            raise exceptions.UnknownContentsType(f'Unknown contents type: {content.content_type}')

        return serializer.serialize_to_view(
            content, user=trans.user, trans=trans, view=view, **serialization_params_dict
        )

    def _parse_legacy_contents_params(self, params: HistoryContentsIndexLegacyParams):
        if params.types:
            types = util.listify(params.types)
        else:
            types = [e.value for e in HistoryContentType]

        details: Any = params.dataset_details
        ids = None
        if params.ids:
            ids = [self.decode_id(EncodedDatabaseIdField(id)) for id in params.ids.split(',')]
            # If explicit ids given, always used detailed result.
            details = 'all'
        else:
            if details and details != 'all':
                details = set(util.listify(details))

        return ParsedHistoryContentsLegacyParams(
            types=types,
            ids=ids,
            deleted=params.deleted,
            visible=params.visible,
            dataset_details=details,
        ).dict(exclude_defaults=True)

    def __collection_dict(self, trans, dataset_collection_instance, **kwds):
        return dictify_dataset_collection_instance(dataset_collection_instance,
            security=trans.security, parent=dataset_collection_instance.history, **kwds)

    def _get_history(self, trans, history_id: EncodedDatabaseIdField) -> History:
        """Retrieves the History with the given ID or raises an error if the current user cannot access it."""
        history = self.history_manager.get_accessible(self.decode_id(history_id), trans.user, current_history=trans.history)
        return history

    def __show_dataset(
        self, trans,
        id: EncodedDatabaseIdField,
        serialization_params: SerializationParams,
    ):
        serialization_params.default_view = "detailed"
        hda = self.hda_manager.get_accessible(self.decode_id(id), trans.user)
        return self.hda_serializer.serialize_to_view(
            hda, user=trans.user, trans=trans, **serialization_params.dict()
        )

    def __show_dataset_collection(
        self, trans,
        id: EncodedDatabaseIdField,
        serialization_params: SerializationParams,
        fuzzy_count: Optional[int] = None,
    ):
        dataset_collection_instance = self.__get_accessible_collection(trans, id)
        view = serialization_params.view or "element"
        return self.__collection_dict(trans, dataset_collection_instance, view=view, fuzzy_count=fuzzy_count)

    def __get_accessible_collection(self, trans, id: EncodedDatabaseIdField):
        return self.dataset_collection_manager.get_dataset_collection_instance(
            trans=trans, instance_type="history", id=id,
        )

    def __create_datasets_from_library_folder(
        self, trans,
        history: History,
        payload: CreateHistoryContentPayloadFromCopy,
        serialization_params: SerializationParams,
    ):
        rval = []
        source = payload.source
        if source == HistoryContentSource.library_folder:
            content = payload.content
            if content is None:
                raise exceptions.RequestParameterMissingException("'content' id of lda or hda is missing")

            folder_id = self.folder_manager.cut_and_decode(trans, content)
            folder = self.folder_manager.get(trans, folder_id)

            current_user_roles = trans.get_current_user_roles()
            security_agent: GalaxyRBACAgent = trans.app.security_agent

            def traverse(folder):
                admin = trans.user_is_admin
                rval = []
                for subfolder in folder.active_folders:
                    if not admin:
                        can_access, folder_ids = security_agent.check_folder_contents(trans.user, current_user_roles, subfolder)
                    if (admin or can_access) and not subfolder.deleted:
                        rval.extend(traverse(subfolder))
                for ld in folder.datasets:
                    if not admin:
                        can_access = security_agent.can_access_dataset(
                            current_user_roles,
                            ld.library_dataset_dataset_association.dataset
                        )
                    if (admin or can_access) and not ld.deleted:
                        rval.append(ld)
                return rval

            for ld in traverse(folder):
                hda = ld.library_dataset_dataset_association.to_history_dataset_association(history, add_to_history=True)
                hda_dict = self.hda_serializer.serialize_to_view(
                    hda, user=trans.user, trans=trans, default_view='detailed', **serialization_params.dict()
                )
                rval.append(hda_dict)
        else:
            message = f"Invalid 'source' parameter in request: {source}"
            raise exceptions.RequestParameterInvalidException(message)

        trans.sa_session.flush()
        return rval

    def __create_dataset(
        self, trans,
        history: History,
        payload: CreateHistoryContentPayloadFromCopy,
        serialization_params: SerializationParams,
    ):
        source = payload.source
        if source not in (HistoryContentSource.library, HistoryContentSource.hda):
            raise exceptions.RequestParameterInvalidException(
                f"'source' must be either 'library' or 'hda': {source}")
        content = payload.content
        if content is None:
            raise exceptions.RequestParameterMissingException("'content' id of lda or hda is missing")

        hda = None
        if source == HistoryContentSource.library:
            hda = self.__create_hda_from_ldda(trans, history, content)
        elif source == HistoryContentSource.hda:
            hda = self.__create_hda_from_copy(trans, history, content)

        if hda is None:
            return None

        trans.sa_session.flush()
        serialization_params.default_view = 'detailed'
        return self.hda_serializer.serialize_to_view(
            hda, user=trans.user, trans=trans, **serialization_params.dict()
        )

    def __create_hda_from_ldda(self, trans, history: History, ldda_id: EncodedDatabaseIdField):
        decoded_ldda_id = self.decode_id(ldda_id)
        ld = self.ldda_manager.get(trans, decoded_ldda_id)
        if type(ld) is not LibraryDataset:
            raise exceptions.RequestParameterInvalidException(
                f"Library content id ( {ldda_id} ) is not a dataset")
        hda = ld.library_dataset_dataset_association.to_history_dataset_association(history, add_to_history=True)
        return hda

    def __create_hda_from_copy(self, trans, history: History, original_hda_id: EncodedDatabaseIdField):
        unencoded_hda_id = self.decode_id(original_hda_id)
        original = self.hda_manager.get_accessible(unencoded_hda_id, trans.user)
        # check for access on history that contains the original hda as well
        self.history_manager.error_unless_accessible(original.history, trans.user, current_history=trans.history)
        hda = self.hda_manager.copy(original, history=history)
        return hda

    def __create_dataset_collection(
        self, trans,
        history: History,
        payload: CreateHistoryContentPayloadFromCollection,
        serialization_params: SerializationParams,
    ):
        """Create hdca in a history from the list of element identifiers

        :param history: history the new hdca should be added to
        :param source: whether to create a new collection or copy existing one
        :type  source: str
        :param payload: dictionary structure containing:
            :param collection_type: type (and depth) of the new collection
            :type name: str
            :param element_identifiers: list of elements that should be in the new collection
                :param element: one member of the collection
                    :param name: name of the element
                    :type name: str
                    :param src: source of the element (hda/ldda)
                    :type src: str
                    :param id: identifier
                    :type id: str
                    :param id: tags
                    :type id: list
                :type element: dict
            :type name: list
            :param name: name of the collection
            :type name: str
            :param hide_source_items: whether to mark the original hdas as hidden
            :type name: bool
            :param copy_elements: whether to copy HDAs when creating collection
            :type name: bool
        :type  payload: dict

       .. note:: Elements may be nested depending on the collection_type

        :returns:   dataset collection information
        :rtype:     dict

        :raises: RequestParameterInvalidException, RequestParameterMissingException
        """
        source = payload.source or HistoryContentSource.new_collection

        dataset_collection_manager = self.dataset_collection_manager
        if source == HistoryContentSource.new_collection:
            create_params = api_payload_to_create_params(payload.dict())
            dataset_collection_instance = dataset_collection_manager.create(
                trans,
                parent=history,
                history=history,
                **create_params
            )
        elif source == HistoryContentSource.hdca:
            content = payload.content
            if content is None:
                raise exceptions.RequestParameterMissingException("'content' id of target to copy is missing")
            dbkey = payload.dbkey
            copy_required = dbkey is not None
            copy_elements = payload.copy_elements or copy_required
            if copy_required and not copy_elements:
                raise exceptions.RequestParameterMissingException("copy_elements passed as 'false' but it is required to change specified attributes")
            dataset_instance_attributes = {}
            if dbkey is not None:
                dataset_instance_attributes["dbkey"] = dbkey
            dataset_collection_instance = dataset_collection_manager.copy(
                trans=trans,
                parent=history,
                source=source,
                encoded_source_id=content,
                copy_elements=copy_elements,
                dataset_instance_attributes=dataset_instance_attributes,
            )
        else:
            message = f"Invalid 'source' parameter in request: {source}"
            raise exceptions.RequestParameterInvalidException(message)

        # if the consumer specified keys or view, use the secondary serializer
        if serialization_params.view or serialization_params.keys:
            serialization_params.default_view = 'detailed'
            return self.hdca_serializer.serialize_to_view(
                dataset_collection_instance, user=trans.user, trans=trans, **serialization_params.dict()
            )

        return self.__collection_dict(trans, dataset_collection_instance, view="element")
