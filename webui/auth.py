"""Authentication: user model, login/logout, password change, role decorators."""

import json
import os
import secrets
from functools import wraps

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from config import USERS_FILE

auth_bp = Blueprint('auth', __name__)
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'warning'


class User(UserMixin):
    def __init__(self, username, role):
        self.id = username
        self.username = username
        self.role = role

    @property
    def is_admin(self):
        return self.role == 'admin'


# ---------------------------------------------------------------------------
# User store helpers
# ---------------------------------------------------------------------------

def _load_users():
    """Load users dict from JSON file."""
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, 'r') as f:
        data = json.load(f)
    return data.get('users', {})


def _save_users(users):
    """Persist users dict to JSON file, preserving secret_key."""
    data = {}
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            data = json.load(f)
    data['users'] = users
    with open(USERS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def init_users():
    """Create default users.json if it doesn't exist."""
    if os.path.exists(USERS_FILE):
        return
    data = {
        'secret_key': secrets.token_hex(32),
        'users': {
            'admin': {
                'password_hash': generate_password_hash('admin123'),
                'role': 'admin',
            },
            'amr': {
                'password_hash': generate_password_hash('amr123'),
                'role': 'amr',
            },
        },
    }
    with open(USERS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def get_secret_key():
    """Read the Flask secret key from users.json."""
    if not os.path.exists(USERS_FILE):
        init_users()
    with open(USERS_FILE, 'r') as f:
        data = json.load(f)
    return data.get('secret_key', 'fallback-secret-key')


# ---------------------------------------------------------------------------
# Flask-Login loader
# ---------------------------------------------------------------------------

@login_manager.user_loader
def load_user(username):
    users = _load_users()
    if username in users:
        return User(username, users[username]['role'])
    return None


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def admin_required(f):
    """Decorator: require the current user to have the admin role."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        users = _load_users()

        if username in users and check_password_hash(users[username]['password_hash'], password):
            user = User(username, users[username]['role'])
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))

        flash('Invalid username or password.', 'danger')

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/api/auth/change-password', methods=['POST'])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')

    if not old_password or not new_password:
        return jsonify({'error': 'Both old and new passwords are required'}), 400

    if len(new_password) < 4:
        return jsonify({'error': 'New password must be at least 4 characters'}), 400

    users = _load_users()
    username = current_user.username

    if username not in users:
        return jsonify({'error': 'User not found'}), 404

    if not check_password_hash(users[username]['password_hash'], old_password):
        return jsonify({'error': 'Current password is incorrect'}), 403

    users[username]['password_hash'] = generate_password_hash(new_password)
    _save_users(users)

    return jsonify({'message': 'Password changed successfully'})
