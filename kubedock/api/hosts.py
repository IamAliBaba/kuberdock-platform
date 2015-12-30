from flask import Blueprint, request
from datetime import datetime

from kubedock.decorators import login_required_or_basic_or_token, maintenance_protected
from kubedock.core import db
from kubedock.nodes.models import RegisteredHost
from kubedock.utils import KubeUtils, atomic
from . import APIError

hosts = Blueprint('hosts', __name__, url_prefix='/hosts')


@hosts.route('/register', methods=['POST'], strict_slashes=False)
@login_required_or_basic_or_token
@maintenance_protected
@KubeUtils.jsonwrap
def create_host():
    user = KubeUtils._get_current_user()
    if not user.is_administrator():
        raise APIError('Insufficient permissions level', 403, type='Permission denied')
    ip = request.environ.get('REMOTE_ADDR')
    register_host(ip)
    return {'ip': ip}


@atomic(nested=False)
def register_host(ip):
    host = RegisteredHost.query.filter_by(host=ip).first()
    if host is not None:
        raise APIError('Host is already registered', 409, type='data exist')
    db.session.add(RegisteredHost(host=ip, time_stamp=datetime.now()))