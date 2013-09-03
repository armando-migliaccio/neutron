# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 VMware, Inc.
# All Rights Reserved
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
#

from oslo.config import cfg

from neutron.openstack.common import log as logging
from neutron.plugins.nicira.common import exceptions

LOG = logging.getLogger(__name__)


def check_services_requirements(cluster):
    ver = cluster.api_client.get_nvp_version()
    if ver.major < 4:
        raise exceptions.NvpInvalidVersion(version=ver)
    cluster_id = cfg.CONF.default_service_cluster_uuid
    if not nvplib.service_cluster_exists(cluster, cluster_id):
        raise exceptions.ServiceClusterUnavailable(cluster_id=cluster_id)


def handle_port_dhcp_access(plugin, context, port_data, action):
    LOG.info('%s port with data %s' % (action, port_data))


def handle_port_metadata_access(context, port, is_delete=False):
    LOG.info('%s port with data %s' % (is_delete, port))


def handle_router_metadata_access(plugin, context, router_id, do_create=True):
    LOG.info('%s router %s' % (do_create, router_id))
