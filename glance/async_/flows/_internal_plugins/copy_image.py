# Copyright 2020 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
import os

import glance_store as store_api
from oslo_config import cfg
from oslo_log import log as logging
from taskflow.patterns import linear_flow as lf
from taskflow import task
from taskflow.types import failure

from glance.common import exception
from glance.i18n import _, _LE

LOG = logging.getLogger(__name__)

CONF = cfg.CONF


class _CopyImage(task.Task):

    default_provides = 'file_uri'

    def __init__(self, task_id, task_type, image_repo, image_id):
        self.task_id = task_id
        self.task_type = task_type
        self.image_repo = image_repo
        self.image_id = image_id
        super(_CopyImage, self).__init__(
            name='%s-CopyImage-%s' % (task_type, task_id))

        self.staging_store = store_api.get_store_from_store_identifier(
            'os_glance_staging_store')

    def execute(self):
        """Create temp file into store and return path to it

        :param image_id: Glance Image ID
        """
        image = self.image_repo.get(self.image_id)
        # NOTE (abhishekk): If ``all_stores_must_succeed`` is set to True
        # and copying task fails then we keep data in staging area as it
        # is so that if second call is made to copy the same image then
        # no need to copy the data in staging area again.
        file_path = "%s/%s" % (getattr(
            CONF, 'os_glance_staging_store').filesystem_store_datadir,
            self.image_id)

        if os.path.exists(file_path):
            return file_path, 0

        # At first search image in default_backend
        default_store = CONF.glance_store.default_backend
        for loc in image.locations:
            if loc['metadata'].get('store') == default_store:
                try:
                    return self._copy_to_staging_store(loc)
                except store_api.exceptions.NotFound:
                    msg = (_LE("Image not present in default store, searching "
                               "in all glance-api specific available "
                               "stores"))
                    LOG.error(msg)
                    break

        available_backends = CONF.enabled_backends
        for loc in image.locations:
            image_backend = loc['metadata'].get('store')
            if (image_backend in available_backends.keys()
                    and image_backend != default_store):
                try:
                    return self._copy_to_staging_store(loc)
                except store_api.exceptions.NotFound:
                    LOG.error(_LE('Image: %(img_id)s is not present in store '
                                  '%(store)s.'),
                              {'img_id': self.image_id,
                               'store': image_backend})
                    continue

        raise exception.NotFound(_("Image not found in any configured "
                                   "store"))

    def _copy_to_staging_store(self, loc):
        store_backend = loc['metadata'].get('store')
        image_data, size = store_api.get(loc['url'], store_backend)
        msg = ("Found image, copying it in staging area")
        LOG.debug(msg)
        return self.staging_store.add(self.image_id, image_data, size)[0]

    def revert(self, result, **kwargs):
        if isinstance(result, failure.Failure):
            LOG.error(_LE('Task: %(task_id)s failed to copy image '
                          '%(image_id)s.'),
                      {'task_id': self.task_id,
                       'image_id': self.image_id})


def get_flow(**kwargs):
    """Return task flow for web-download.

    :param task_id: Task ID.
    :param task_type: Type of the task.
    :param image_repo: Image repository used.
    :param uri: URI the image data is downloaded from.
    """
    task_id = kwargs.get('task_id')
    task_type = kwargs.get('task_type')
    image_repo = kwargs.get('image_repo')
    image_id = kwargs.get('image_id')

    return lf.Flow(task_type).add(
        _CopyImage(task_id, task_type, image_repo, image_id),
    )
