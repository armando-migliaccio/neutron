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

from sqlalchemy import Column
from sqlalchemy import ForeignKey
from sqlalchemy import orm
from sqlalchemy import String

from neutron.common import exceptions as n_exc
from neutron.db import models_v2


class LsnPort(models_v2.model_base.BASEV2):
    lsn_port_id = Column(String(36), primary_key=True)
    lsn_id = Column(String(36), ForeignKey('lsn.lsn_id', ondelete="CASCADE"))
    sub_id = Column(String(36), nullable=False)

    def __init__(self, lsn_port_id, lsn_id, subnet_id):
        self.lsn_port_id = lsn_port_id
        self.lsn_id = lsn_id
        self.sub_id = subnet_id


class Lsn(models_v2.model_base.BASEV2):
    lsn_id = Column(String(36), primary_key=True)
    net_id = Column(String(36), nullable=False)

    def __init__(self, net_id, lsn_id):
        self.net_id = net_id
        self.lsn_id = lsn_id


def lsn_create(context, network_id, lsn_id, lsn_port_id=None):
    with context.session.begin(subtransactions=True):
        lsn = Lsn(lsn_id, network_id)
        context.session.add(lsn)
        if lsn_port_id:
            lsn_port = LsnPort(lsn_port_id, lsn_id)
            context.session.add(lsn_port)


def lsn_delete(context, lsn_id):
    with context.session.begin(subtransactions=True):
        (context.session.query(Lsn).
         filter_by(lsn_id=lsn_id).delete())


def lsn_delete_for_network(context, network_id):
    with context.session.begin(subtransactions=True):
        (context.session.query(Lsn).
         filter_by(net_id=network_id).delete())


def lsn_get_for_network(context, network_id, raise_on_missing=True):
    query = context.session.query(Lsn)
    try:
        return query.filter_by(net_id=network_id).one()
    except orm.exc.NoResultFound:
        if raise_on_missing:
            raise n_exc.NotFound


def lsn_exists_for_network(context, network_id):
    return lsn_get_for_network(
        context, network_id, raise_on_missing=False) is not None


def lsn_port_add_for_lsn(context, lsn_port_id, lsn_id, subnet_id):
    with context.session.begin(subtransactions=True):
        lsn_port = LsnPort(lsn_port_id, lsn_id, subnet_id)
        context.session.add(lsn_port)


def lsn_port_get_for_subnet(context, lsn_id, subnet_id, raise_on_missing=True):
    with context.session.begin(subtransactions=True):
        try:
            (context.session.query(LsnPort).
             filter_by(lsn_id=lsn_id).filter_by(sub_id=subnet_id).one())
        except orm.exc.NoResultFound:
            if raise_on_missing:
                raise n_exc.NotFound


def lsn_port_delete_for_lsn(context, lsn_id):
    with context.session.begin(subtransactions=True):
        (context.session.query(LsnPort).
         filter_by(lsn_id=lsn_id).delete())
