import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import and_, or_, select
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
app = Flask(__name__)
secret = os.environ.get('SECRET_KEY')
if not secret:
    if os.environ.get('FLASK_DEBUG') == '1':
        secret = 'development-only-change-me'
    else:
        raise RuntimeError('Set a strong SECRET_KEY environment variable')
app.config.update(
    SECRET_KEY=secret,
    SQLALCHEMY_DATABASE_URI=os.environ.get('DATABASE_URL', '').replace('postgres://', 'postgresql+psycopg://', 1).replace('postgresql://', 'postgresql+psycopg://', 1),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '1') == '1',
    SESSION_COOKIE_SAMESITE='Lax',
    WTF_CSRF_TIME_LIMIT=3600,
    MAX_CONTENT_LENGTH=64 * 1024,
)
if not app.config['SQLALCHEMY_DATABASE_URI']:
    raise RuntimeError('Set DATABASE_URL to your PostgreSQL database URL')
if os.environ.get('BEHIND_PROXY', '0') == '1':
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=['180 per hour'], storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'))
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

class Charger(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    title = db.Column(db.String(110), nullable=False)
    area = db.Column(db.String(100), nullable=False, index=True)
    address = db.Column(db.String(255), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    connector = db.Column(db.String(35), nullable=False)
    power_kw = db.Column(db.Float, nullable=False)
    price_per_hour = db.Column(db.Numeric(9, 2), nullable=False)
    is_available = db.Column(db.Boolean, nullable=False, default=True)
    owner = db.relationship('User', backref='chargers')

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    charger_id = db.Column(db.Integer, db.ForeignKey('charger.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    start_at = db.Column(db.DateTime(timezone=True), nullable=False)
    end_at = db.Column(db.DateTime(timezone=True), nullable=False)
    total_price = db.Column(db.Numeric(9, 2), nullable=False)
    status = db.Column(db.String(15), nullable=False, default='confirmed')
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    charger = db.relationship('Charger', backref='bookings')
    user = db.relationship('User', backref='bookings')

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id)) if user_id.isdecimal() else None

def role_required(role):
    def wrapper(fn):
        @wraps(fn)
        @login_required
        def inner(*args, **kwargs):
            if current_user.role != role:
                abort(403)
            return fn(*args, **kwargs)
        return inner
    return wrapper

def parse_time(value):
    # Browser datetime-local is interpreted as UTC in this MVP; UI explicitly labels it UTC.
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

@app.context_processor
def globals_for_templates():
    return {'carto_key': os.environ.get('CARTO_BASEMAP_KEY', ''), 'now_utc': datetime.now(timezone.utc)}

@app.after_request
def harden(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self' https://unpkg.com; img-src 'self' data: https://*.cartocdn.com https://*.openstreetmap.org https://unpkg.com; connect-src 'self' https://*.cartocdn.com; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    return response

@app.get('/health')
def health():
    db.session.execute(select(1))
    return {'status': 'ok'}

@app.get('/')
def home():
    area = request.args.get('area', '').strip()[:100]
    chargers_query = Charger.query.filter_by(is_available=True)
    if area:
        chargers_query = chargers_query.filter(Charger.area.ilike(f'%{area.replace("%", "").replace("_", "")}%'))
    chargers = chargers_query.order_by(Charger.id.desc()).limit(100).all()
    return render_template('home.html', chargers=chargers, area=area)

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit('10 per hour', methods=['POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()[:100]
        email = request.form.get('email', '').strip().lower()[:255]
        password = request.form.get('password', '')
        role = request.form.get('role', '')
        if not name or '@' not in email or role not in ('renter', 'user') or len(password) < 12 or len(password) > 128:
            flash('Provide a name, valid email, role, and password of 12–128 characters.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
        else:
            user = User(name=name, email=email, password_hash=generate_password_hash(password), role=role)
            db.session.add(user)
            try:
                db.session.commit()
                login_user(user)
                return redirect(url_for('dashboard'))
            except Exception:
                db.session.rollback()
                flash('Could not register. Try another email.', 'error')
    return render_template('auth.html', mode='register')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('12 per hour', methods=['POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, request.form.get('password', '')):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('auth.html', mode='login')

@app.post('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.get('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'renter':
        chargers = Charger.query.filter_by(owner_id=current_user.id).order_by(Charger.id.desc()).all()
        bookings = Booking.query.join(Charger).filter(Charger.owner_id == current_user.id).order_by(Booking.start_at.desc()).limit(100).all()
    else:
        chargers = []
        bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.start_at.desc()).limit(100).all()
    return render_template('dashboard.html', chargers=chargers, bookings=bookings)

@app.route('/chargers/new', methods=['GET', 'POST'])
@role_required('renter')
def new_charger():
    if request.method == 'POST':
        try:
            lat = float(request.form['latitude'])
            lon = float(request.form['longitude'])
            power = float(request.form['power_kw'])
            price = Decimal(request.form['price_per_hour'])
            title = request.form.get('title', '').strip()[:110]
            area = request.form.get('area', '').strip()[:100]
            address = request.form.get('address', '').strip()[:255]
            connector = request.form.get('connector', '')
            assert title and area and address and connector in ('Type 2', 'CCS2', 'CHAdeMO', '15A socket', 'Other')
            assert -90 <= lat <= 90 and -180 <= lon <= 180 and 0 < power <= 500 and Decimal('0') < price <= Decimal('100000') and price.is_finite()
            charger = Charger(owner_id=current_user.id, title=title, area=area, address=address, latitude=lat, longitude=lon, power_kw=power, price_per_hour=price, connector=connector)
            db.session.add(charger)
            db.session.commit()
            flash('Charger listed successfully.', 'success')
            return redirect(url_for('dashboard'))
        except (ValueError, KeyError, InvalidOperation, AssertionError):
            flash('Please check all charger details and coordinates.', 'error')
    return render_template('new_charger.html')

@app.post('/chargers/<int:charger_id>/toggle')
@role_required('renter')
def toggle(charger_id):
    charger = db.session.get(Charger, charger_id)
    if not charger or charger.owner_id != current_user.id:
        abort(404)
    charger.is_available = not charger.is_available
    db.session.commit()
    flash('Listing availability updated.', 'success')
    return redirect(url_for('dashboard'))

@app.get('/chargers/<int:charger_id>')
def charger_detail(charger_id):
    charger = db.get_or_404(Charger, charger_id)
    return render_template('detail.html', charger=charger)

@app.post('/chargers/<int:charger_id>/book')
@role_required('user')
@limiter.limit('20 per hour')
def book(charger_id):
    try:
        start, end = parse_time(request.form['start_at']), parse_time(request.form['end_at'])
        hours = (end - start).total_seconds() / 3600
        if start <= datetime.now(timezone.utc) or not 0 < hours <= 24:
            raise ValueError()
    except (ValueError, KeyError, OverflowError):
        flash('Select a future start and end (UTC), maximum 24 hours.', 'error')
        return redirect(url_for('charger_detail', charger_id=charger_id))
    # Row lock serializes competing requests for the same charger in PostgreSQL.
    with db.session.begin_nested():
        charger = db.session.execute(select(Charger).where(Charger.id == charger_id).with_for_update()).scalar_one_or_none()
        if not charger or not charger.is_available:
            flash('This charger is unavailable.', 'error')
            return redirect(url_for('home'))
        conflict = db.session.execute(select(Booking.id).where(and_(Booking.charger_id == charger_id, Booking.status == 'confirmed', Booking.start_at < end, Booking.end_at > start)).limit(1)).first()
        if conflict:
            flash('That time overlaps an existing booking. Choose another slot.', 'error')
            return redirect(url_for('charger_detail', charger_id=charger_id))
        amount = (charger.price_per_hour * Decimal(str(hours))).quantize(Decimal('0.01'))
        db.session.add(Booking(charger_id=charger_id, user_id=current_user.id, start_at=start, end_at=end, total_price=amount))
    db.session.commit()
    flash('Booking confirmed. No payment was collected.', 'success')
    return redirect(url_for('dashboard'))

@app.post('/bookings/<int:booking_id>/cancel')
@login_required
def cancel_booking(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    if current_user.id != booking.user_id and current_user.id != booking.charger.owner_id:
        abort(403)
    if booking.status != 'confirmed' or booking.start_at <= datetime.now(timezone.utc):
        flash('Only future confirmed bookings can be cancelled.', 'error')
    else:
        booking.status = 'cancelled'
        db.session.commit()
        flash('Booking cancelled.', 'success')
    return redirect(url_for('dashboard'))

@app.get('/api/chargers')
def api_chargers():
    chargers = Charger.query.filter_by(is_available=True).order_by(Charger.id.desc()).limit(500).all()
    return jsonify([{'id': c.id, 'title': c.title, 'area': c.area, 'latitude': c.latitude, 'longitude': c.longitude, 'connector': c.connector, 'power_kw': c.power_kw, 'price_per_hour': str(c.price_per_hour), 'url': url_for('charger_detail', charger_id=c.id)} for c in chargers])

@app.cli.command('init-db')
def init_db():
    db.create_all()
    print('Database tables created')

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
