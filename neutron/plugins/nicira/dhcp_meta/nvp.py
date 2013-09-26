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

from neutron.api.v2 import attributes as attr
from neutron.common import constants as const
from neutron.common import exceptions as n_exc
from neutron.db import db_base_plugin_v2
from neutron.db import l3_db
from neutron.openstack.common import log as logging
from neutron.plugins.nicira.common import exceptions as p_exc
from neutron.plugins.nicira.nsxlib import lsn as lsn_api
from neutron.plugins.nicira import nvplib


LOG = logging.getLogger(__name__)
METADATA_MAC = "aa:bb:cc:dd:ee:ff"
METADATA_PORT_ID = 'metadata:id'
METADATA_PORT_NAME = 'metadata:name'
METADATA_DEVICE_ID = 'metadata:device'

dhcp_opts = [
    cfg.ListOpt('extra_domain_name_servers',
                default=None,
                help=_('Comma separated list of additional '
                       'domain name servers')),
    cfg.StrOpt('domain_name',
               default='openstacklocal',
               help=_('Domain to use for building the hostnames')),
    cfg.IntOpt('default_lease_time', default=43200,
               help=_("Default DHCP lease time")),
]


metadata_opts = [
    cfg.StrOpt('metadata_server_address', default='127.0.0.1',
               help=_("IP address used by Metadata server.")),
    cfg.IntOpt('metadata_server_port',
               default=8775,
               help=_("TCP Port used by Metadata server.")),
    cfg.StrOpt('metadata_shared_secret',
               default='',
               help=_('Shared secret to sign instance-id request'),
               secret=True)
]


def register_dhcp_opts(config):
    config.CONF.register_opts(dhcp_opts, "NVP_DHCP")


def register_metadata_opts(config):
    config.CONF.register_opts(metadata_opts, "NVP_METADATA")


class LsnManager(object):
    """Manage LSN entities associated with networks."""

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def cluster(self):
        return self.plugin.cluster

    def lsn_get(self, context, network_id, raise_on_err=True):
        """Retrieve the LSN id associated to the network."""
        try:
            return lsn_api.lsn_for_network_get(self.cluster, network_id)
        except (n_exc.NotFound, nvplib.NvpApiClient.NvpApiException):
            logger = raise_on_err and LOG.error or LOG.warn
            logger(_('Unable to find Logical Service Node for '
                     'network %s'), network_id)
            if raise_on_err:
                raise p_exc.LsnNotFound(entity='network',
                                        entity_id=network_id)

    def lsn_create(self, context, network_id):
        """Create a LSN associated to the network."""
        try:
            return lsn_api.lsn_for_network_create(self.cluster, network_id)
        except nvplib.NvpApiClient.NvpApiException:
            err_msg = _('Unable to create LSN for network %s') % network_id
            raise p_exc.NvpPluginException(err_msg=err_msg)

    def lsn_delete(self, context, lsn_id):
        """Delete a LSN given its id."""
        try:
            lsn_api.lsn_delete(self.cluster, lsn_id)
        except (n_exc.NotFound, nvplib.NvpApiClient.NvpApiException):
            LOG.warn(_('Unable to delete Logical Service Node %s'), lsn_id)

    def lsn_delete_by_network(self, context, network_id):
        """Delete a LSN associated to the network."""
        try:
            lsn_id = lsn_api.lsn_for_network_get(context, network_id)
        except (n_exc.NotFound, nvplib.NvpApiClient.NvpApiException):
            LOG.warn(_('Unable to find Logical Service Node for '
                       'network %s'), network_id)
        else:
            self.lsn_delete(context, lsn_id)

    def lsn_port_get(self, context, network_id, subnet_id, raise_on_err=True):
        """Retrieve LSN and LSN port for the network and the subnet."""
        lsn_id = self.lsn_get(context, network_id, raise_on_err=raise_on_err)
        if lsn_id:
            try:
                lsn_port_id = lsn_api.lsn_port_by_subnet_get(
                    self.cluster, lsn_id, subnet_id)
            except (n_exc.NotFound, nvplib.NvpApiClient.NvpApiException):
                logger = raise_on_err and LOG.error or LOG.warn
                logger(_('Unable to find Logical Service Node Port for '
                         'LSN %(lsn_id)s and subnet %(subnet_id)s')
                       % {'lsn_id': lsn_id, 'subnet_id': subnet_id})
                if raise_on_err:
                    raise p_exc.LsnPortNotFound(lsn_id=lsn_id,
                                                entity='subnet',
                                                entity_id=subnet_id)
                return (lsn_id, None)
            else:
                return (lsn_id, lsn_port_id)
        else:
            return (None, None)

    def lsn_port_create(self, context, lsn_id, subnet_info):
        """Create and return LSN port for associated subnet."""
        try:
            return lsn_api.lsn_port_create(self.cluster, lsn_id, subnet_info)
        except n_exc.NotFound:
            raise p_exc.LsnNotFound(entity='', entity_id=lsn_id)
        except nvplib.NvpApiClient.NvpApiException:
            err_msg = _('Unable to create port for LSN  %s') % lsn_id
            raise p_exc.NvpPluginException(err_msg=err_msg)

    def lsn_port_delete(self, context, lsn_id, lsn_port_id):
        try:
            lsn_api.lsn_port_delete(self.cluster, lsn_id, lsn_port_id)
        except (n_exc.NotFound, nvplib.NvpApiClient.NvpApiException):
            LOG.warn(_('Unable to delete LSN Port %s'), lsn_port_id)

    def lsn_port_setup(
        self, context, network_id, port_id, port_data, subnet_config=None):
        """Connect network to LSN via specified port and port_data."""
        try:
            lsn_id = None
            lswitch_port_id = nvplib.get_port_by_neutron_tag(
                self.cluster, network_id, port_id)['uuid']
            lsn_id = self.lsn_get(context, network_id)
            lsn_port_id = self.lsn_port_create(context, lsn_id, port_data)
        except (n_exc.NotFound, p_exc.NvpPluginException):
            raise p_exc.PortConfigurationError(
                net_id=network_id, lsn_id=lsn_id, port_id=port_id)
        else:
            try:
                lsn_api.lsn_port_plug_network(
                    self.cluster, lsn_id, lsn_port_id, lswitch_port_id)
            except p_exc.LsnConfigurationConflict:
                self.lsn_port_delete(self.cluster, lsn_id, lsn_port_id)
                raise p_exc.PortConfigurationError(
                    net_id=network_id, lsn_id=lsn_id, port_id=port_id)
            if subnet_config:
                self.lsn_port_dhcp_configure(
                    context, lsn_id, lsn_port_id, subnet_config)
            else:
                return (lsn_id, lsn_port_id)

    def lsn_port_dhcp_configure(self, context, lsn_id, lsn_port_id, subnet):
        """Enable/disable dhcp services with the given config options."""
        is_enabled = subnet["enable_dhcp"]
        dhcp_options = {
            "domain_name": cfg.CONF.NVP_DHCP.domain_name,
            "default_lease_time": cfg.CONF.NVP_DHCP.default_lease_time,
        }
        dns_servers = cfg.CONF.NVP_DHCP.extra_domain_name_servers or []
        dns_servers.extend(subnet["dns_nameservers"])
        if subnet['gateway_ip']:
            dhcp_options["routers"] = subnet["gateway_ip"]
        if dns_servers:
            dhcp_options["domain_name_servers"] = ",".join(dns_servers)
        if subnet["host_routes"]:
            dhcp_options["classless_static_routes"] = (
                ",".join(subnet["host_routes"])
            )
        try:
            lsn_api.lsn_port_dhcp_configure(
                self.cluster, lsn_id, lsn_port_id, is_enabled, dhcp_options)
        except (n_exc.NotFound, nvplib.NvpApiClient.NvpApiException):
            err_msg = (_('Unable to configure dhcp for Logical Service '
                         'Node %(lsn_id)s and port %(lsn_port_id)s')
                       % {'lsn_id': lsn_id, 'lsn_port_id': lsn_port_id})
            LOG.error(err_msg)
            raise p_exc.NvpPluginException(err_msg=err_msg)

    def lsn_port_dispose(self, context, network_id, subnet_id):
        lsn_id, lsn_port_id = self.lsn_port_get(
            context, network_id, subnet_id, raise_on_err=False)
        if lsn_port_id:
            try:
                lsn_api.lsn_port_delete(self.cluster, lsn_id, lsn_port_id)
            except nvplib.NvpApiClient.NvpApiException:
                LOG.warn(_('Unable to delete LSN port %s'), lsn_port_id)

    def _lsn_port_host_conf(self, context, network_id, subnet_id, data, hdlr):
        lsn_id = None
        lsn_port_id = None
        try:
            lsn_id, lsn_port_id = self.lsn_port_get(
                context, network_id, subnet_id)
            hdlr(self.cluster, lsn_id, lsn_port_id, data)
        except (n_exc.NotFound, nvplib.NvpApiClient.NvpApiException):
            LOG.error(_('Error while configuring LSN '
                        'port %s'), lsn_port_id)
            raise p_exc.PortConfigurationError(
                net_id=network_id, lsn_id=lsn_id, port_id=lsn_port_id)

    def lsn_port_dhcp_host_add(self, context, network_id, subnet_id, host):
        """Add dhcp host entry from LSN port configuration."""
        self._lsn_port_host_conf(context, network_id, subnet_id, host,
                                 lsn_api.lsn_port_dhcp_host_add)

    def lsn_port_dhcp_host_remove(self, context, network_id, subnet_id, host):
        """Remove dhcp host entry from LSN port configuration."""
        self._lsn_port_host_conf(context, network_id, subnet_id, host,
                                 lsn_api.lsn_port_dhcp_host_remove)


class DhcpAgentNotifyAPI(object):

    def __init__(self, plugin, lsn_manager):
        self.plugin = plugin
        self.lsn_manager = lsn_manager
        self._handle_subnet_dhcp_access = {'create': self._subnet_create,
                                           'update': self._subnet_update,
                                           'delete': self._subnet_delete}

    def notify(self, context, data, methodname):
        [resource, action, _e] = methodname.split('.')
        if resource == 'subnet':
            self._handle_subnet_dhcp_access[action](context, data['subnet'])
        elif resource == 'port' and action == 'update':
            self._port_update(context, data['port'])

    def _port_update(self, context, port):
        network_id = port['network_id']
        try:
            lsn_id = lsn_api.lsn_for_network_get(
                self.plugin.cluster, network_id)
        except n_exc.NotFound:
            LOG.warn(_("Unable to find Logical Services "
                       "Node for network %s"), network_id)
            raise
        else:
            subnet_id = port["fixed_ips"][0]['subnet_id']
            try:
                lsn_port_id = lsn_api.lsn_port_by_subnet_get(
                    self.plugin.cluster, lsn_id, subnet_id)
            except n_exc.NotFound:
                lsn_port_id = lsn_api.lsn_port_by_mac_get(
                    self.plugin.cluster, lsn_id, METADATA_MAC)
            filters = {'network_id': [network_id]}
            ports = self.plugin.get_ports(context, filters)
            if not ports:
                return
            dhcp_entries = [
                {'mac_address': p['mac_address'],
                 'ip_address': p["fixed_ips"][0]['ip_address']}
                for p in ports
                if (p.get("fixed_ips") and
                    p['device_owner'] != const.DEVICE_OWNER_DHCP and
                    p['device_owner'] != l3_db.DEVICE_OWNER_ROUTER_INTF)
            ]
            if dhcp_entries:
                lsn_api.lsn_port_host_entries_update(self.plugin.cluster,
                                                     lsn_id, lsn_port_id,
                                                     'dhcp', dhcp_entries)
            meta_entries = [
                {'instance_id': p['device_id'],
                 'ip_address': p["fixed_ips"][0]['ip_address']}
                for p in ports
                if (p['device_id'] and
                    p.get('fixed_ips') and
                    p['device_owner'] != const.DEVICE_OWNER_DHCP and
                    p['device_owner'] != l3_db.DEVICE_OWNER_ROUTER_INTF)
            ]
            if meta_entries:
                lsn_api.lsn_port_host_entries_update(self.plugin.cluster,
                                                     lsn_id, lsn_port_id,
                                                     'metadata-proxy',
                                                     meta_entries)

    def _subnet_create(self, context, subnet, clean_on_err=True):
        if subnet['enable_dhcp']:
            network_id = subnet['network_id']
            # Create port for DHCP service
            dhcp_port = {
                "name": "",
                "admin_state_up": True,
                "device_id": "",
                "device_owner": const.DEVICE_OWNER_DHCP,
                "network_id": network_id,
                "tenant_id": subnet["tenant_id"],
                "mac_address": attr.ATTR_NOT_SPECIFIED,
                "fixed_ips": [{"subnet_id": subnet['id']}]
            }
            try:
                # This will end up calling handle_port_dhcp_access
                # down below as well as handle_port_metadata_access
                self.plugin.create_port(context, {'port': dhcp_port})
            except p_exc.PortConfigurationError as e:
                err_msg = (_("Error while creating subnet %(cidr)s for "
                             "network %(network)s. Please, contact "
                             "administrator") %
                           {"cidr": subnet["cidr"],
                            "network": network_id})
                LOG.error(err_msg)
                db_base_plugin_v2.NeutronDbPluginV2.delete_port(
                    self.plugin, context, e.port_id)
                if clean_on_err:
                    self.plugin.delete_subnet(context, subnet['id'])
                raise

    def _subnet_update(self, context, subnet):
        network_id = subnet['network_id']
        try:
            lsn_id, lsn_port_id = self.lsn_manager.lsn_port_get(
                context, network_id, subnet['id'])
            self.lsn_manager.lsn_port_dhcp_configure(
                lsn_id, lsn_port_id, subnet)
        except p_exc.LsnPortNotFound:
            # It's possible that the subnet was created with dhcp off;
            # check if the subnet was uplinked onto a router, and if so
            # remove the patch attachment between the metadata port and
            # the lsn port, in favor on the one we'll be creating during
            # _subnet_create
            _destroy_metadata_owner_port(self.plugin, lsn_id, network_id)
            self._subnet_create(context, subnet, clean_on_err=False)

    def _subnet_delete(self, context, subnet):
        # FIXME(armando-migliaccio): it looks like that a subnet filter
        # is ineffective; so filter by network for now.
        network_id = subnet['network_id']
        filters = dict(network_id=[network_id],
                       device_owner=[const.DEVICE_OWNER_DHCP])
        # FIXME(armando-migliaccio): this may be race-y
        ports = self.plugin.get_ports(context, filters=filters)
        if ports:
            # This will end up calling handle_port_dhcp_access
            # down below as well as handle_port_metadata_access
            self.plugin.delete_port(context, ports[0]['id'])


def check_services_requirements(cluster):
    ver = cluster.api_lsn_api.get_nvp_version()
    # It sounds like 4.1 is the first one where DHCP in NSX/NVP
    # will have the experimental feature
    if ver.major >= 4 and ver.minor >= 1:
        cluster_id = cfg.CONF.default_service_cluster_uuid
        if not lsn_api.service_cluster_exists(cluster, cluster_id):
            raise p_exc.ServiceClusterUnavailable(cluster_id=cluster_id)
    else:
        raise p_exc.NvpInvalidVersion(version=ver)


def handle_network_dhcp_access(plugin, context, network, action):
    LOG.info(_("Performing DHCP %(action)s for resource: %(resource)s")
             % {"action": action, "resource": network})
    if action == 'create_network':
        network_id = network['id']
        plugin.lsn_manager.lsn_create(context, network_id)
    elif action == 'delete_network':
        # NOTE(armando-migliaccio): on delete_network, network
        # is just the network id
        network_id = network
        plugin.lsn_manager.lsn_delete_by_network(context, network_id)
    LOG.info(_("Logical Services Node for network "
               "%s configured successfully"), network_id)


def handle_port_dhcp_access(plugin, context, port, action):
    LOG.info(_("Performing DHCP %(action)s for resource: %(resource)s")
             % {"action": action, "resource": port})
    if port["device_owner"] == const.DEVICE_OWNER_DHCP:
        network_id = port["network_id"]
        # If the device owner is DHCP, the port must have
        # a subnet assigned as well as a fixed ip
        subnet_id = port["fixed_ips"][0]['subnet_id']
        if action == "create_port":
            subnet = plugin.get_subnet(context, subnet_id)
            subnet_data = {
                "mac_address": port["mac_address"],
                "ip_address": subnet['cidr'],
                "subnet_id": subnet['id']
            }
            plugin.lsn_manager.lsn_port_setup(
                context, network_id, port['id'], subnet_data, subnet)
        elif action == "delete_port":
            plugin.lsn_manager.lsn_port_dispose(context, network_id, subnet_id)
    elif port["device_owner"] != const.DEVICE_OWNER_DHCP:
        # check that the port has an IP and that DHCP is enabled
        # otherwise, bail
        if port.get("fixed_ips"):
            subnet_id = port["fixed_ips"][0]['subnet_id']
            if not plugin.get_subnet(context, subnet_id)['enable_dhcp']:
                LOG.info(_("DHCP is disabled: nothing to do"))
                return
            host_data = {
                "mac_address": port["mac_address"],
                "ip_address": port["fixed_ips"][0]['ip_address']
            }
            network_id = port["network_id"]
            if action == "create_port":
                handler = plugin.lsn_manager.lsn_port_dhcp_host_add
            elif action == "delete_port":
                handler = plugin.lsn_manager.lsn_port_dhcp_host_remove
            try:
                handler(context, network_id, subnet_id, host_data)
            except p_exc.PortConfigurationError:
                db_base_plugin_v2.NeutronDbPluginV2.delete_port(
                    plugin, context, port['id'])
                raise
    LOG.info(_("DHCP for port %s configured successfully"), port['id'])


def handle_port_metadata_access(plugin, context, port, is_delete=False):
    if (port["device_owner"] != const.DEVICE_OWNER_DHCP and
        port["device_owner"] != l3_db.DEVICE_OWNER_ROUTER_INTF and
        port["device_id"]):
        # TODO(armando-migliaccio): store network->LSN->LSN_Port mapping
        # to avoid hitting NSX/NVP more than it is strictly necessary
        network_id = port["network_id"]
        lsn_id = lsn_api.lsn_for_network_get(plugin.cluster, network_id)
        subnet_id = port["fixed_ips"][0]['subnet_id']
        try:
            lsn_port_id = lsn_api.lsn_port_by_subnet_get(
                plugin.cluster, lsn_id, subnet_id)
        except n_exc.NotFound:
            lsn_port_id = lsn_api.lsn_port_by_mac_get(
                plugin.cluster, lsn_id, METADATA_MAC)
        data = {
            "instance_id": port["device_id"],
            "ip_address": port["fixed_ips"][0]['ip_address']
        }
        if not is_delete:
            LOG.info(_("Adding metadata entry for port %s"), port['id'])
            lsn_api.lsn_port_metadata_host_add(
                plugin.cluster, lsn_id, lsn_port_id, data)
        else:
            try:
                LOG.info(_("Removing metadata entry for port %s"), port['id'])
                lsn_api.lsn_port_metadata_host_remove(
                    plugin.cluster, lsn_id, lsn_port_id, data)
            except n_exc.NotFound:
                # do not fail if the port has been removed already
                LOG.warn(_("Unable to remove metadata entry for "
                           " port %s"), port['id'])
                return
        LOG.info(_("Port %(port)s configured successfully with "
                   "metadata %(data)s") % {'port': port['id'], "data": data})


def handle_router_metadata_access(plugin, context, router_id, interface=None):
    LOG.info(_("Handle metadata access via router: %(r)s and "
               "interface %(i)s") % {'r': router_id, 'i': interface})
    if interface:
        subnet = plugin.get_subnet(context, interface['subnet_id'])
        network_id = subnet['network_id']
        try:
            lsn_id = lsn_api.lsn_for_network_get(plugin.cluster, network_id)
        except n_exc.NotFound:
            LOG.warn(_("Unable to find Logical Services "
                       "Node for network %s"), network_id)
            raise
        else:
            try:
                plugin.get_port(context, interface['port_id'])
                is_enabled = True
            except n_exc.PortNotFound:
                is_enabled = False
            _configure_metadata_services(plugin, subnet, lsn_id, is_enabled)
    LOG.info(_("Metadata for router %s handled successfully"), router_id)


def _create_metadata_owner_port(plugin, lsn_id, subnet):
    data = {
        "mac_address": METADATA_MAC,
        "ip_address": subnet['cidr'],
        "subnet_id": subnet['id']
    }
    lsn_port_id = lsn_api.lsn_port_create(plugin.cluster, lsn_id, data)
    lswitch_port_id = nvplib.create_lport(
        plugin.cluster, subnet['network_id'], subnet['tenant_id'],
        METADATA_PORT_ID, METADATA_PORT_NAME,
        METADATA_DEVICE_ID, True)['uuid']
    lsn_api.lsn_port_plug_network(
        plugin.cluster, lsn_id, lsn_port_id, lswitch_port_id)


def _destroy_metadata_owner_port(plugin, lsn_id, network_id):
    try:
        lsn_port_id = lsn_api.lsn_port_by_mac_get(
            plugin.cluster, lsn_id, METADATA_MAC)
        lsn_api.lsn_port_delete(plugin.cluster, lsn_id, lsn_port_id)
    except n_exc.NotFound:
        LOG.warning(_("Unable to find Logical Services Node "
                      "Port with MAC %s"), METADATA_MAC)
    else:
        try:
            lswitch_port_id = nvplib.get_port_by_neutron_tag(
                plugin.cluster, network_id, METADATA_PORT_ID)['uuid']
            nvplib.delete_port(
                plugin.cluster, network_id, lswitch_port_id)
        except n_exc.PortNotFoundOnNetwork:
            LOG.warning(_("Metadata port not found while attempting "
                          "to delete it from network %s"), network_id)


def _configure_metadata_services(plugin, subnet, lsn_id, is_enabled):
    metadata_options = {
        'metadata_server_ip': cfg.CONF.NVP_METADATA.metadata_server_address,
        'metadata_server_port': cfg.CONF.NVP_METADATA.metadata_server_port,
        'metadata_proxy_shared_secret': (
            cfg.CONF.NVP_METADATA.metadata_shared_secret
        )
    }
    lsn_api.lsn_metadata_configure(
        plugin.cluster, lsn_id, is_enabled, metadata_options)
    if is_enabled:
        # if subnet had dhcp off when created, no lsn port exists,
        # so create one, and wire it
        try:
            lsn_api.lsn_port_by_subnet_get(
                plugin.cluster, lsn_id, subnet['id'])
        except n_exc.NotFound:
            _create_metadata_owner_port(plugin, lsn_id, subnet)
    else:
        # otherwise, remove the metadata port if it exists
        _destroy_metadata_owner_port(plugin, lsn_id, subnet['network_id'])
    LOG.info(_("Metadata access for LSN %s configured successfully"), lsn_id)
