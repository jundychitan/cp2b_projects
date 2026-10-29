"""AMR Gateway Web UI - Flask Application."""

from flask import redirect, url_for
from flask_login import login_required

from auth import auth_bp, get_secret_key, init_users, login_manager
from blueprints.lte import lte_bp
from blueprints.rtu import rtu_bp
from blueprints.amr import amr_bp
from blueprints.livemeter import livemeter_bp
from blueprints.diagnostic import diagnostic_bp

from flask import Flask


def create_app():
    # Ensure default users exist
    init_users()

    app = Flask(__name__)
    app.config['SECRET_KEY'] = get_secret_key()

    # Initialize Flask-Login
    login_manager.init_app(app)

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(lte_bp)
    app.register_blueprint(rtu_bp)
    app.register_blueprint(amr_bp)
    app.register_blueprint(livemeter_bp)
    app.register_blueprint(diagnostic_bp)

    # Root route - redirect to LTE tab (default)
    @app.route('/')
    @login_required
    def index():
        from flask import render_template
        from flask_login import current_user
        return render_template('base.html', active_tab='lte', user=current_user)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
