from flask import Flask, render_template, request, redirect, url_for, abort, flash, send_file, jsonify
from sqlalchemy import create_engine, or_, and_, not_
from sqlalchemy.orm import sessionmaker, joinedload
from models import Base, EagleTrustFundDonor, EagleTrustFundTransaction
from dotenv import load_dotenv
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import ceil
import csv
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from datetime import timedelta

# Load environment variables
load_dotenv()

# Flask application setup
app = Flask(__name__)

# Set the secret key - first try environment variable, then use a default for development
if not os.getenv("FLASK_SECRET_KEY"):
    print("Warning: FLASK_SECRET_KEY not set in .env file. Using default key for development.")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-key-please-change-in-production")

# Enable debug mode if specified in environment
app.debug = os.getenv("FLASK_DEBUG", "False").lower() in ("true", "1", "t")

# Database connection parameters
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Constants for pagination
ITEMS_PER_PAGE = 50

# SQLAlchemy engine and session factory
engine = create_engine(DATABASE_URL)
Base.metadata.bind = engine
Session = sessionmaker(bind=engine)

def parse_multi_values(value_string):
    """Parse comma-separated values and return list"""
    if not value_string:
        return []
    return [v.strip() for v in value_string.split(',') if v.strip()]

def parse_date_range(date_str):
    """Parse date or date range string"""
    if not date_str:
        return None, None
    
    if ' to ' in date_str:
        parts = date_str.split(' to ')
        try:
            start_date = datetime.strptime(parts[0].strip(), '%Y-%m-%d').date() if parts[0].strip() else None
            end_date = datetime.strptime(parts[1].strip(), '%Y-%m-%d').date() if parts[1].strip() else None
            return start_date, end_date
        except ValueError:
            return None, None
    else:
        try:
            single_date = datetime.strptime(date_str.strip(), '%Y-%m-%d').date()
            return single_date, single_date
        except ValueError:
            return None, None

def parse_amount_range(amount_str):
    """Parse amount or amount range string"""
    if not amount_str:
        return None, None
    
    if ' to ' in amount_str:
        parts = amount_str.split(' to ')
        try:
            min_amount = Decimal(parts[0].strip()) if parts[0].strip() else None
            max_amount = Decimal(parts[1].strip()) if parts[1].strip() else None
            return min_amount, max_amount
        except InvalidOperation:
            return None, None
    else:
        try:
            single_amount = Decimal(amount_str.strip())
            return single_amount, single_amount
        except InvalidOperation:
            return None, None

def build_search_query(session, search_params):
    """Helper function to build search query based on parameters"""
    query = session.query(EagleTrustFundDonor)
    field_conditions = []
    filter_conditions = []
    query_joined_transactions = False
    transaction_fields_provided = False

    # Add filter conditions first
    if search_params.get('exclude_deceased') == 'true':
        filter_conditions.append(
            not_(EagleTrustFundDonor.newsletter_status_desc.ilike("DECEASED OR UNDELIVERABLE"))
        )
    if search_params.get('exclude_non_donors') == 'true':
        filter_conditions.append(
            not_(EagleTrustFundDonor.donor_status_desc.ilike("NON DONOR (DO NOT SOLICIT)"))
        )

    # Apply filter conditions
    if filter_conditions:
        query = query.filter(and_(*filter_conditions))

    # Add search conditions (only if specific search fields are provided)
    search_field_provided = False
    
    # Handle multi-value fields
    if search_params.get('alternate_id'):
        field_conditions.append(EagleTrustFundDonor.alternate_id.ilike(f"%{search_params['alternate_id']}%"))
        search_field_provided = True
    if search_params.get('first_name'):
        field_conditions.append(EagleTrustFundDonor.first_name.ilike(f"%{search_params['first_name']}%"))
        search_field_provided = True
    if search_params.get('last_name'):
        field_conditions.append(EagleTrustFundDonor.last_name.ilike(f"%{search_params['last_name']}%"))
        search_field_provided = True
    if search_params.get('email'):
        field_conditions.append(EagleTrustFundDonor.email_address.ilike(f"%{search_params['email']}%"))
        search_field_provided = True
    
    # Handle multiple cities
    if search_params.get('city'):
        cities = parse_multi_values(search_params['city'])
        if cities:
            city_conditions = [EagleTrustFundDonor.city.ilike(f"%{city}%") for city in cities]
            field_conditions.append(or_(*city_conditions))
            search_field_provided = True
    
    # Handle multiple states
    if search_params.get('state'):
        states = parse_multi_values(search_params['state'])
        if states:
            state_conditions = [EagleTrustFundDonor.state.ilike(f"%{state}%") for state in states]
            field_conditions.append(or_(*state_conditions))
            search_field_provided = True
    
    # Handle multiple zip codes
    if search_params.get('zip_code'):
        zip_codes = parse_multi_values(search_params['zip_code'])
        if zip_codes:
            zip_conditions = [EagleTrustFundDonor.zip_plus4.ilike(f"%{zip_code}%") for zip_code in zip_codes]
            field_conditions.append(or_(*zip_conditions))
            search_field_provided = True
    
    if search_params.get('phone'):
        phone_search = f"%{search_params['phone']}%"
        field_conditions.append(or_(
            EagleTrustFundDonor.phone.ilike(phone_search),
            EagleTrustFundDonor.work_phone.ilike(phone_search),
            EagleTrustFundDonor.cell_phone.ilike(phone_search)
        ))
        search_field_provided = True

    # Handle donor status filtering (multiple values)
    if search_params.get('donor_status'):
        statuses = parse_multi_values(search_params['donor_status'])
        if statuses:
            status_conditions = [EagleTrustFundDonor.donor_status.ilike(f"%{status}%") for status in statuses]
            field_conditions.append(or_(*status_conditions))
            search_field_provided = True

    # Handle newsletter status filtering (multiple values)
    if search_params.get('newsletter_status'):
        statuses = parse_multi_values(search_params['newsletter_status'])
        if statuses:
            status_conditions = [EagleTrustFundDonor.newsletter_status.ilike(f"%{status}%") for status in statuses]
            field_conditions.append(or_(*status_conditions))
            search_field_provided = True

    # Handle total donation amount range
    if search_params.get('total_amount_range'):
        min_amount, max_amount = parse_amount_range(search_params['total_amount_range'])
        if min_amount is not None:
            field_conditions.append(EagleTrustFundDonor.total_dollar_amount >= min_amount)
            search_field_provided = True
        if max_amount is not None:
            field_conditions.append(EagleTrustFundDonor.total_dollar_amount <= max_amount)
            search_field_provided = True

    # Handle date added range
    if search_params.get('date_added_range'):
        start_date, end_date = parse_date_range(search_params['date_added_range'])
        if start_date is not None:
            field_conditions.append(EagleTrustFundDonor.date_added_to_database >= start_date.strftime('%Y-%m-%d'))
            search_field_provided = True
        if end_date is not None:
            field_conditions.append(EagleTrustFundDonor.date_added_to_database <= end_date.strftime('%Y-%m-%d'))
            search_field_provided = True

    # Handle expiration date range
    if search_params.get('expiration_date_range'):
        start_date, end_date = parse_date_range(search_params['expiration_date_range'])
        if start_date is not None:
            field_conditions.append(EagleTrustFundDonor.expiration_date >= start_date.strftime('%Y-%m-%d'))
            search_field_provided = True
        if end_date is not None:
            field_conditions.append(EagleTrustFundDonor.expiration_date <= end_date.strftime('%Y-%m-%d'))
            search_field_provided = True

    # Process transaction search parameters
    if search_params.get('trans_date'):
        try:
            trans_date_val = datetime.strptime(search_params['trans_date'], '%Y-%m-%d').date()
            field_conditions.append(EagleTrustFundTransaction.trans_date == trans_date_val)
            transaction_fields_provided = True
        except ValueError:
            flash("Invalid transaction date format. Please use YYYY-MM-DD.", "error")

    if search_params.get('trans_amount'):
        try:
            trans_amount_val = Decimal(search_params['trans_amount'])
            field_conditions.append(EagleTrustFundTransaction.trans_amount == trans_amount_val)
            transaction_fields_provided = True
        except InvalidOperation:
            flash("Invalid transaction amount format.", "error")

    # Process text-based transaction fields
    transaction_text_fields = [
        ('appeal_code', EagleTrustFundTransaction.appeal_code),
        ('payment_type', EagleTrustFundTransaction.payment_type),
        ('update_batch_num', EagleTrustFundTransaction.update_batch_num),
        ('bluebook_job_description', EagleTrustFundTransaction.bluebook_job_description),
        ('bluebook_list_description', EagleTrustFundTransaction.bluebook_list_description)
    ]

    for field_name, field_column in transaction_text_fields:
        if search_params.get(field_name):
            field_conditions.append(field_column.ilike(f"%{search_params[field_name]}%"))
            transaction_fields_provided = True

    # Join with transactions table if any transaction fields were provided
    if transaction_fields_provided:
        query = query.join(EagleTrustFundTransaction, EagleTrustFundDonor.base_donor_id == EagleTrustFundTransaction.base_donor_id).distinct()
        query_joined_transactions = True

    # Apply search conditions only if specific fields were searched
    if field_conditions:
        query = query.filter(and_(*field_conditions))

    # Return the query and whether any search field was provided
    return query, search_field_provided or transaction_fields_provided

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        # Get all search parameters
        search_params = {
            'donor_id': request.form.get("donor_id", "").strip(),
            'alternate_id': request.form.get("alternate_id", "").strip(),
            'first_name': request.form.get("first_name", "").strip(),
            'last_name': request.form.get("last_name", "").strip(),
            'email': request.form.get("email", "").strip(),
            'city': request.form.get("city", "").strip(),
            'state': request.form.get("state", "").strip(),
            'zip_code': request.form.get("zip_code", "").strip(),
            'phone': request.form.get("phone", "").strip(),
            'exclude_deceased': request.form.get("exclude_deceased"),
            'exclude_non_donors': request.form.get("exclude_non_donors"),
            'hidden_columns': request.form.get("hidden_columns", "secondary_title,secondary_first_name,secondary_last_name,secondary_suffix"),
            # Status parameters
            'donor_status': request.form.get("donor_status", "").strip(),
            'newsletter_status': request.form.get("newsletter_status", "").strip(),
            # Range parameters
            'total_amount_range': request.form.get("total_amount_range", "").strip(),
            'date_added_range': request.form.get("date_added_range", "").strip(),
            'expiration_date_range': request.form.get("expiration_date_range", "").strip(),
            # Transaction search parameters
            'trans_date': request.form.get("trans_date", "").strip(),
            'trans_amount': request.form.get("trans_amount", "").strip(),
            'appeal_code': request.form.get("appeal_code", "").strip(),
            'payment_type': request.form.get("payment_type", "").strip(),
            'update_batch_num': request.form.get("update_batch_num", "").strip(),
            'bluebook_job_description': request.form.get("bluebook_job_description", "").strip(),
            'bluebook_list_description': request.form.get("bluebook_list_description", "").strip(),
        }

        # If donor_id is provided, redirect directly to donor page
        if search_params['donor_id']:
            try:
                donor_id = int(search_params['donor_id'])
                return redirect(url_for("donor", donor_id=donor_id))
            except ValueError:
                abort(400, "Invalid donor ID")

        # Create session
        session = Session()
        
        try:
            # Build and execute search query
            query, search_field_provided = build_search_query(session, search_params)
            
            # If no search parameters provided (excluding filter options)
            if not search_field_provided:
                flash("Please enter at least one search criterion (e.g., Name, City, Email, or Transaction details).", "warning")
                return render_template("index.html", search_params=search_params)

            # Get page number from request
            page = request.form.get('page', 1, type=int)
            
            # Count total results
            total_results = query.count()
            total_pages = ceil(total_results / ITEMS_PER_PAGE)
            
            # Apply pagination
            results = query.offset((page - 1) * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE).all()

            # If only one result found, redirect to that donor's page
            if total_results == 1:
                donor_id = results[0].base_donor_id
                session.close()
                return redirect(url_for("donor", donor_id=donor_id))
            
            # If multiple results, show results page
            return render_template(
                "search_results.html",
                donors=results,
                search_params=search_params,
                current_page=page,
                total_pages=total_pages,
                total_results=total_results
            )

        finally:
            session.close()

    # Pass default filter values for GET request
    default_search_params = {
        'exclude_deceased': 'true',
        'exclude_non_donors': 'true',
        'hidden_columns': 'secondary_title,secondary_first_name,secondary_last_name,secondary_suffix'
    }
    return render_template("index.html", search_params=default_search_params)

@app.route("/refine_search", methods=["POST"])
def refine_search():
    # Get current search parameters
    search_params = {}
    for key, value in request.form.items():
        if key.startswith('current_'):
            search_params[key[8:]] = value  # Remove 'current_' prefix

    # Add new search parameter
    field = request.form.get('field')
    value = request.form.get('value')
    if field and value:
        search_params[field] = value

    # Create session
    session = Session()
    
    try:
        # Build and execute search query
        query, search_field_provided = build_search_query(session, search_params)
        
        # Get page number from request
        page = request.form.get('page', 1, type=int)
        
        # Count total results
        total_results = query.count()
        total_pages = ceil(total_results / ITEMS_PER_PAGE)
        
        # Apply pagination
        results = query.offset((page - 1) * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE).all()
        
        return render_template(
            "search_results.html", 
            donors=results, 
            search_params=search_params,
            current_page=page,
            total_pages=total_pages,
            total_results=total_results
        )
    finally:
        session.close()

@app.route("/toggle_column", methods=["POST"])
def toggle_column():
    # Get current search parameters
    search_params = {}
    for key, value in request.form.items():
        if key.startswith('current_'):
            search_params[key[8:]] = value  # Remove 'current_' prefix

    # Get column and action
    column_id = request.form.get('column_id')
    action = request.form.get('action')

    # Update hidden columns
    hidden_columns = search_params.get('hidden_columns', '').split(',') if search_params.get('hidden_columns') else []
    if action == 'hide' and column_id not in hidden_columns:
        hidden_columns.append(column_id)
    elif action == 'show' and column_id in hidden_columns:
        hidden_columns.remove(column_id)
    
    # Update search params with new hidden columns
    search_params['hidden_columns'] = ','.join(filter(None, hidden_columns))

    # Create session
    session = Session()
    
    try:
        # Build and execute search query
        query, search_field_provided = build_search_query(session, search_params)
        
        # Get page number from request
        page = request.form.get('page', 1, type=int)
        
        # Count total results
        total_results = query.count()
        total_pages = ceil(total_results / ITEMS_PER_PAGE)
        
        # Apply pagination
        results = query.offset((page - 1) * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE).all()
        
        return render_template(
            "search_results.html", 
            donors=results, 
            search_params=search_params,
            current_page=page,
            total_pages=total_pages,
            total_results=total_results
        )
    finally:
        session.close()

@app.route("/donor/<int:donor_id>")
def donor(donor_id):
    session = Session()
    try:
        donor = (session.query(EagleTrustFundDonor)
                      .options(joinedload(EagleTrustFundDonor.transactions))
                      .filter_by(base_donor_id=donor_id)
                      .first())
        
        if donor is None:
            abort(404, f"Donor #{donor_id} not found")

        # Get gift information
        gifted_to = None
        if donor.gifted_to_donor_id:
            gifted_to = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor.gifted_to_donor_id).first()
        
        # Get who gifted to this donor
        gifted_by = session.query(EagleTrustFundDonor).filter_by(gifted_to_donor_id=donor_id).first()

        return render_template(
            "donor.html",
            donor=donor,
            transactions=donor.transactions,
            gifted_to=gifted_to,
            gifted_by=gifted_by
        )
    finally:
        session.close()

# Helper function to extract donor data from form
def extract_donor_data(form):
    return {
        'base_donor_id': form.get("base_donor_id", "").strip() or None,
        'alternate_id': form.get("alternate_id", "").strip() or None,
        'name_prefix': form.get("name_prefix", "").strip() or None,
        'first_name': form.get("first_name", "").strip() or None,
        'last_name': form.get("last_name", "").strip() or None,
        'suffix': form.get("suffix", "").strip() or None,
        'formatted_full_name': form.get("formatted_full_name", "").strip() or None,
        'secondary_title': form.get("secondary_title", "").strip() or None,
        'secondary_first_name': form.get("secondary_first_name", "").strip() or None,
        'secondary_last_name': form.get("secondary_last_name", "").strip() or None,
        'secondary_suffix': form.get("secondary_suffix", "").strip() or None,
        'secondary_full_name': form.get("secondary_full_name", "").strip() or None,
        'address_1_company': form.get("address_1_company", "").strip() or None,
        'address_2_secondary': form.get("address_2_secondary", "").strip() or None,
        'address_3_primary': form.get("address_3_primary", "").strip() or None,
        'city': form.get("city", "").strip() or None,
        'state': form.get("state", "").strip().upper() or None,
        'zip_plus4': form.get("zip_plus4", "").strip() or None,
        'country': form.get("country", "").strip() or None,
        'phone': form.get("phone", "").strip() or None,
        'work_phone': form.get("work_phone", "").strip() or None,
        'cell_phone': form.get("cell_phone", "").strip() or None,
        'salutation_dear': form.get("salutation_dear", "").strip() or None,
        'removal_request_note': form.get("removal_request_note", "").strip() or None,
        'twitter': form.get("twitter", "").strip() or None,
        'gender_code': form.get("gender_code", "").strip() or None,
        'birth_date': form.get("birth_date") or None,
        'newsletter_status': form.get("newsletter_status", "").strip() or None,
        'newsletter_status_desc': form.get("newsletter_status_desc", "").strip() or None,
        'donor_status': form.get("donor_status", "").strip() or None,
        'donor_status_desc': form.get("donor_status_desc", "").strip() or None,
        'date_added_to_database': form.get("date_added_to_database") or None,
        'email_address': form.get("email_address", "").strip() or None,
        'interest_borders': form.get("interest_borders", "").strip() or None,
        'interest_pro_life': form.get("interest_pro_life", "").strip() or None,
        'interest_eagle_council': form.get("interest_eagle_council", "").strip() or None,
        'interest_topic_1': form.get("interest_topic_1", "").strip() or None,
        'interest_topic_2': form.get("interest_topic_2", "").strip() or None,
        'interest_topic_3': form.get("interest_topic_3", "").strip() or None,
        'interest_topic_4': form.get("interest_topic_4", "").strip() or None,
        'education_reporter_status': form.get("education_reporter_status", "").strip() or None,
        'expiration_date': form.get("expiration_date") or None,
        'news_and_notes_status': form.get("news_and_notes_status", "").strip() or None,
        'rnc_life_status': form.get("rnc_life_status", "").strip() or None,
        'eagle_status': form.get("eagle_status", "").strip() or None,
        'eagle_state_president': form.get("eagle_state_president", "").strip() or None,
        'flag': form.get("flag", "").strip() or None,
        'changed': form.get("changed", "").strip() or None,
        'interest': form.get("interest", "").strip() or None,
        'house_publications': form.get("house_publications", "").strip() or None,
    }

# Helper function to parse date strings
def parse_date(date_str):
    if not date_str:
        return None
    try:
        # Adjust format as needed based on expected input 'YYYY-MM-DD'
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None

@app.route("/add_donor", methods=["GET", "POST"])
def add_donor():
    if request.method == "POST":
        session = Session()
        try:
            donor_data = extract_donor_data(request.form)

            # Basic validation (e.g., check for last name)
            if not donor_data.get('last_name'):
                flash("Last name is required.", "error")
                return render_template("add_donor.html", donor=donor_data)

            # Validate and handle base_donor_id
            base_donor_id = None
            if donor_data.get('base_donor_id'):
                try:
                    base_donor_id = int(donor_data['base_donor_id'])
                    # Check if this donor ID already exists
                    existing_donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=base_donor_id).first()
                    if existing_donor:
                        flash(f"Donor ID {base_donor_id} already exists. Please choose a different ID.", "error")
                        return render_template("add_donor.html", donor=donor_data)
                except ValueError:
                    flash("Invalid donor ID. Please enter a valid number.", "error")
                    return render_template("add_donor.html", donor=donor_data)

            # Remove base_donor_id from donor_data since we'll set it separately
            donor_data_copy = donor_data.copy()
            donor_data_copy.pop('base_donor_id', None)

            # Create new donor object
            new_donor = EagleTrustFundDonor(**donor_data_copy)

            # Set the base_donor_id if provided
            if base_donor_id:
                new_donor.base_donor_id = base_donor_id

            # Handle date conversions (adjust field names as needed)
            new_donor.birth_date = parse_date(donor_data.get('birth_date'))
            # Auto-fill date_added_to_database with today's date if not provided
            new_donor.date_added_to_database = parse_date(donor_data.get('date_added_to_database')) or datetime.now().date()
            new_donor.expiration_date = parse_date(donor_data.get('expiration_date'))

            session.add(new_donor)
            session.commit()
            flash(f"Donor '{new_donor.first_name} {new_donor.last_name}' added successfully!", "success")
            return redirect(url_for("donor", donor_id=new_donor.base_donor_id))
        except Exception as e:
            session.rollback()
            app.logger.error(f"Error adding donor: {e}")
            flash(f"Error adding donor: {e}", "error")
            return render_template("add_donor.html", donor=request.form)
        finally:
            session.close()
    else:
        # GET request - auto-fill base_donor_id with max + 1
        session = Session()
        try:
            # Get the maximum base_donor_id from the database (excluding anonymous donor ID 999,999,999)
            max_donor_id = session.query(EagleTrustFundDonor.base_donor_id)\
                .filter(EagleTrustFundDonor.base_donor_id != 999999999)\
                .order_by(EagleTrustFundDonor.base_donor_id.desc()).first()
            next_donor_id = (max_donor_id[0] + 1) if max_donor_id and max_donor_id[0] else 1
            
            return render_template("add_donor.html", donor={'base_donor_id': next_donor_id})
        finally:
            session.close()

@app.route("/donor/<int:donor_id>/edit", methods=["GET", "POST"])
def edit_donor(donor_id):
    session = Session()
    try:
        donor_to_edit = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
        if not donor_to_edit:
            abort(404, f"Donor #{donor_id} not found")

        if request.method == "POST":
            donor_data = extract_donor_data(request.form)

            # Basic validation
            if not donor_data.get('last_name'):
                flash("Last name is required.", "error")
                current_data = donor_to_edit.__dict__
                current_data.update(donor_data)
                return render_template("edit_donor.html", donor=current_data)

            # Update donor object fields (excluding base_donor_id which should never be modified)
            for key, value in donor_data.items():
                if key == 'base_donor_id':
                    continue  # Skip base_donor_id - it's the primary key and should never be updated
                elif key in ['birth_date', 'date_added_to_database', 'expiration_date']:
                    setattr(donor_to_edit, key, parse_date(value))
                elif hasattr(donor_to_edit, key):
                    setattr(donor_to_edit, key, value)

            session.commit()
            flash(f"Donor #{donor_id} updated successfully!", "success")
            return redirect(url_for("donor", donor_id=donor_id))
        
        else:
            return render_template("edit_donor.html", donor=donor_to_edit)

    except Exception as e:
        session.rollback()
        app.logger.error(f"Error editing donor {donor_id}: {e}")
        flash(f"Error updating donor: {e}", "error")
        if request.method == "POST":
            # Merge form data with existing donor data for display, but preserve the donor object structure
            current_data = donor_to_edit.__dict__.copy() if 'donor_to_edit' in locals() else {}
            form_data = extract_donor_data(request.form)
            current_data.update(form_data)
            # Ensure base_donor_id is preserved from the original donor
            current_data['base_donor_id'] = donor_id
            return render_template("edit_donor.html", donor=current_data)
        else:
            return redirect(url_for('home'))
    finally:
        session.close()

@app.route("/donor/<int:donor_id>/add_transaction", methods=["GET", "POST"])
def add_transaction(donor_id):
    session = Session()
    try:
        donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
        if not donor:
            abort(404, f"Donor #{donor_id} not found")

        if request.method == "POST":
            trans_date_str = request.form.get('trans_date')
            trans_amount_str = request.form.get('trans_amount', "").strip()
            
            errors = []
            trans_date = parse_date(trans_date_str)
            if not trans_date:
                errors.append("Transaction date is required and must be in YYYY-MM-DD format.")
            
            trans_amount = None
            if not trans_amount_str:
                errors.append("Transaction amount is required.")
            else:
                try:
                    trans_amount = Decimal(trans_amount_str)
                    if trans_amount < 0:
                        errors.append("Transaction amount cannot be negative.")
                except InvalidOperation:
                    errors.append("Invalid transaction amount.")
            
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("add_transaction.html", donor_id=donor_id, transaction=request.form)

            new_transaction = EagleTrustFundTransaction(
                base_donor_id=donor_id,
                trans_date=trans_date,
                trans_amount=trans_amount,
                appeal_code=request.form.get('appeal_code', "").strip() or None,
                payment_type=request.form.get('payment_type', "").strip() or None,
                update_batch_num=request.form.get('update_batch_num', "").strip() or None,
                bluebook_job_description=request.form.get('bluebook_job_description', "").strip() or None,
                bluebook_list_description=request.form.get('bluebook_list_description', "").strip() or None,
                payment_method=request.form.get('payment_method', "").strip() or None
            )

            # Update donor's transaction summary fields
            # Update latest transaction
            if donor.latest_date is None or trans_date > parse_date(donor.latest_date):
                donor.latest_date = trans_date.strftime('%Y-%m-%d')
                donor.latest_amount = trans_amount
                
            # Update largest transaction
            if donor.largest_amount is None or trans_amount > donor.largest_amount:
                donor.largest_amount = trans_amount
                donor.largest_date = trans_date.strftime('%Y-%m-%d')

            # Update first/inception transaction
            if donor.inception_date is None or trans_date < parse_date(donor.inception_date):
                donor.inception_amount = trans_amount
                donor.inception_date = trans_date.strftime('%Y-%m-%d')

            # Update total amounts and response counts
            donor.total_dollar_amount = (donor.total_dollar_amount or Decimal('0')) + trans_amount
            donor.total_responses_includes_zero = (donor.total_responses_includes_zero or 0) + 1
            if trans_amount > 0:
                donor.total_responses_non_zero = (donor.total_responses_non_zero or 0) + 1

            session.add(new_transaction)
            session.commit()
            flash(f"Transaction added successfully for donor #{donor_id}!", "success")
            return redirect(url_for("donor", donor_id=donor_id))

        else:
            return render_template("add_transaction.html", donor_id=donor_id, transaction={})

    except Exception as e:
        session.rollback()
        app.logger.error(f"Error adding transaction for donor {donor_id}: {e}")
        flash(f"Error adding transaction: {e}", "error")
        if request.method == "POST":
            return render_template("add_transaction.html", donor_id=donor_id, transaction=request.form)
        else:
            return redirect(url_for('donor', donor_id=donor_id))
    finally:
        session.close()

@app.route("/donor_quick_add", methods=["GET", "POST"])
def donor_quick_add():
    session = Session()
    try:
        # Get the starting donor ID (max + 1) for both GET and POST (excluding anonymous donor ID 999,999,999)
        max_donor_id = session.query(EagleTrustFundDonor.base_donor_id)\
            .filter(EagleTrustFundDonor.base_donor_id != 999999999)\
            .order_by(EagleTrustFundDonor.base_donor_id.desc()).first()
        next_donor_id = (max_donor_id[0] + 1) if max_donor_id and max_donor_id[0] else 1
        
        if request.method == "POST":
            # Get number of donor rows
            max_row = 0
            for key in request.form.keys():
                if key.startswith('first_name_'):
                    try:
                        row_num = int(key.split('_')[-1])
                        max_row = max(max_row, row_num)
                    except ValueError:
                        continue
            
            if max_row == 0:
                flash("Please add at least one donor.", "warning")
                return render_template("donor_quick_add.html", form_data=request.form, next_donor_id=next_donor_id)
            
            donors_to_add = []
            errors = []
            used_donor_ids = set()
            
            # Process each donor row
            for i in range(1, max_row + 1):
                first_name = request.form.get(f'first_name_{i}', '').strip()
                last_name = request.form.get(f'last_name_{i}', '').strip()
                salutation = request.form.get(f'salutation_{i}', '').strip() or None
                email = request.form.get(f'email_{i}', '').strip() or None
                phone = request.form.get(f'phone_{i}', '').strip() or None
                company = request.form.get(f'company_{i}', '').strip() or None  # This is address_1_company
                address = request.form.get(f'address_{i}', '').strip() or None  # This is address_3_primary
                city = request.form.get(f'city_{i}', '').strip() or None
                state = request.form.get(f'state_{i}', '').strip().upper() or None
                zip_code = request.form.get(f'zip_code_{i}', '').strip() or None
                newsletter_status = request.form.get(f'newsletter_status_{i}', '').strip() or None
                donor_id_str = request.form.get(f'donor_id_{i}', '').strip()
                
                # Skip empty rows
                if not first_name and not last_name and not donor_id_str:
                    continue
                
                # Validate required fields
                if not last_name:
                    errors.append(f"Row {i}: Last name is required")
                    continue
                
                # Validate and handle donor ID
                if not donor_id_str:
                    errors.append(f"Row {i}: Donor ID is required")
                    continue
                
                try:
                    donor_id = int(donor_id_str)
                except ValueError:
                    errors.append(f"Row {i}: Invalid donor ID '{donor_id_str}'")
                    continue
                
                # Check for duplicate donor IDs in this batch
                if donor_id in used_donor_ids:
                    errors.append(f"Row {i}: Duplicate donor ID {donor_id} in this batch")
                    continue
                
                # Check if donor ID already exists in database
                existing_donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
                if existing_donor:
                    errors.append(f"Row {i}: Donor ID {donor_id} already exists in database")
                    continue
                
                used_donor_ids.add(donor_id)
                
                # Map newsletter status to donor status and descriptions
                status_mapping = {
                    'A': ('A', 'ACTIVE', 'ACTIVE'),
                    'L': ('L', 'LIFETIME', 'LIFETIME'), 
                    'X': ('X', 'EXPIRED', 'EXPIRED'),
                    'M': ('M', 'MUTINY', 'MUTINY'),
                    'E': ('E', 'EXEMPT', 'EXEMPT')
                }
                
                donor_status, donor_status_desc, newsletter_status_desc = status_mapping.get(
                    newsletter_status, ('A', 'ACTIVE', 'ACTIVE')
                )
                
                # Create new donor object
                new_donor = EagleTrustFundDonor(
                    base_donor_id=donor_id,
                    first_name=first_name or None,
                    last_name=last_name,
                    salutation_dear=salutation,
                    email_address=email,
                    phone=phone,
                    address_1_company=company,
                    address_3_primary=address,
                    city=city,
                    state=state,
                    zip_plus4=zip_code,
                    newsletter_status=newsletter_status,
                    newsletter_status_desc=newsletter_status_desc,
                    donor_status=donor_status,
                    donor_status_desc=donor_status_desc,
                    date_added_to_database=datetime.now().date(),
                    # Set reasonable defaults
                    total_dollar_amount=Decimal('0'),
                    total_responses_includes_zero=0,
                    total_responses_non_zero=0
                )
                
                donors_to_add.append(new_donor)
            
            # If there were validation errors, return with errors
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("donor_quick_add.html", form_data=request.form, next_donor_id=next_donor_id)
            
            if not donors_to_add:
                flash("No valid donors to process.", "warning")
                return render_template("donor_quick_add.html", form_data=request.form, next_donor_id=next_donor_id)
            
            # Add all donors to session
            for donor in donors_to_add:
                session.add(donor)
            
            # Commit all changes
            session.commit()
            
            flash(f"Successfully added {len(donors_to_add)} donors!", "success")
            return redirect(url_for("donor_quick_add"))
        
        # GET request - show empty form with next donor ID
        return render_template("donor_quick_add.html", form_data={}, next_donor_id=next_donor_id)
        
    except Exception as e:
        if request.method == "POST":
            session.rollback()
            app.logger.error(f"Error adding donors: {e}")
            flash(f"Error adding donors: {e}", "error")
            return render_template("donor_quick_add.html", form_data=request.form, next_donor_id=next_donor_id)
        else:
            app.logger.error(f"Error loading donor quick add: {e}")
            flash(f"Error loading page: {e}", "error")
            return redirect(url_for("home"))
    finally:
        session.close()

@app.route("/batch_transactions", methods=["GET", "POST"])
def batch_transactions():
    if request.method == "POST":
        # Get global settings
        trans_date_str = request.form.get('global_trans_date', '').strip()
        update_batch_num = request.form.get('global_update_batch_num', '').strip() or None
        payment_method = request.form.get('global_payment_method', '').strip() or None
        
        # Validate global trans_date
        trans_date = parse_date(trans_date_str)
        if not trans_date:
            flash("Transaction date is required and must be in YYYY-MM-DD format.", "error")
            return render_template("batch_transactions.html", form_data=request.form)
        
        # Get number of transaction rows
        max_row = 0
        for key in request.form.keys():
            if key.startswith('donor_id_'):
                try:
                    row_num = int(key.split('_')[-1])
                    max_row = max(max_row, row_num)
                except ValueError:
                    continue
        
        if max_row == 0:
            flash("Please add at least one transaction.", "warning")
            return render_template("batch_transactions.html", form_data=request.form)
        
        session = Session()
        try:
            transactions_to_add = []
            donors_to_update = {}
            errors = []
            
            # Process each transaction row
            for i in range(1, max_row + 1):
                donor_id_str = request.form.get(f'donor_id_{i}', '').strip()
                amount_str = request.form.get(f'trans_amount_{i}', '').strip()
                appeal_code = request.form.get(f'appeal_code_{i}', '').strip() or None
                payment_type = request.form.get(f'payment_type_{i}', '').strip() or None
                job_description = request.form.get(f'bluebook_job_description_{i}', '').strip() or None
                list_description = request.form.get(f'bluebook_list_description_{i}', '').strip() or None
                
                # Skip empty rows
                if not donor_id_str and not amount_str:
                    continue
                
                # Validate donor ID
                if not donor_id_str:
                    errors.append(f"Row {i}: Donor ID is required")
                    continue
                
                try:
                    donor_id = int(donor_id_str)
                except ValueError:
                    errors.append(f"Row {i}: Invalid donor ID '{donor_id_str}'")
                    continue
                
                # Check if donor exists
                donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
                if not donor:
                    errors.append(f"Row {i}: Donor #{donor_id} not found")
                    continue
                
                # Validate amount
                if not amount_str:
                    errors.append(f"Row {i}: Transaction amount is required")
                    continue
                
                try:
                    trans_amount = Decimal(amount_str)
                    if trans_amount < 0:
                        errors.append(f"Row {i}: Transaction amount cannot be negative")
                        continue
                except InvalidOperation:
                    errors.append(f"Row {i}: Invalid transaction amount '{amount_str}'")
                    continue
                
                # Create transaction object
                new_transaction = EagleTrustFundTransaction(
                    base_donor_id=donor_id,
                    trans_date=trans_date,
                    trans_amount=trans_amount,
                    appeal_code=appeal_code,
                    payment_type=payment_type,
                    update_batch_num=update_batch_num,
                    bluebook_job_description=job_description,
                    bluebook_list_description=list_description,
                    payment_method=payment_method
                )
                
                transactions_to_add.append(new_transaction)
                
                # Track donors that need summary updates
                if donor_id not in donors_to_update:
                    donors_to_update[donor_id] = donor
            
            # If there were validation errors, return with errors but preserve form data
            if errors:
                for error in errors:
                    flash(error, "error")
                
                # Preserve all form data including transaction rows
                preserved_data = dict(request.form)
                
                # Get the max row number to recreate the same structure
                max_row = 0
                for key in request.form.keys():
                    if key.startswith('donor_id_'):
                        try:
                            row_num = int(key.split('_')[-1])
                            max_row = max(max_row, row_num)
                        except ValueError:
                            continue
                
                preserved_data['max_row'] = max_row
                return render_template("batch_transactions.html", form_data=preserved_data)
            
            if not transactions_to_add:
                flash("No valid transactions to process.", "warning")
                return render_template("batch_transactions.html", form_data=request.form)
            
            # Add all transactions to session
            for transaction in transactions_to_add:
                session.add(transaction)
            
            # Update donor summary fields for each affected donor
            for donor_id, donor in donors_to_update.items():
                donor_transactions = [t for t in transactions_to_add if t.base_donor_id == donor_id]
                
                for transaction in donor_transactions:
                    # Update latest transaction
                    if donor.latest_date is None or trans_date > parse_date(donor.latest_date):
                        donor.latest_date = trans_date.strftime('%Y-%m-%d')
                        donor.latest_amount = transaction.trans_amount
                    
                    # Update largest transaction
                    if donor.largest_amount is None or transaction.trans_amount > donor.largest_amount:
                        donor.largest_amount = transaction.trans_amount
                        donor.largest_date = trans_date.strftime('%Y-%m-%d')
                    
                    # Update first/inception transaction
                    if donor.inception_date is None or trans_date < parse_date(donor.inception_date):
                        donor.inception_amount = transaction.trans_amount
                        donor.inception_date = trans_date.strftime('%Y-%m-%d')
                    
                    # Update total amounts and response counts
                    donor.total_dollar_amount = (donor.total_dollar_amount or Decimal('0')) + transaction.trans_amount
                    donor.total_responses_includes_zero = (donor.total_responses_includes_zero or 0) + 1
                    if transaction.trans_amount > 0:
                        donor.total_responses_non_zero = (donor.total_responses_non_zero or 0) + 1
            
            # Commit all changes
            session.commit()
            
            # Redirect to batch success page with batch information
            return redirect(url_for("batch_success", 
                                  batch_num=update_batch_num or 'NOBATCH',
                                  num_transactions=len(transactions_to_add),
                                  total_amount=float(sum(t.trans_amount for t in transactions_to_add))))
            
        except Exception as e:
            session.rollback()
            app.logger.error(f"Error processing batch transactions: {e}")
            flash(f"Error processing batch transactions: {e}", "error")
            
            # Preserve all form data including transaction rows
            preserved_data = dict(request.form)
            
            # Get the max row number to recreate the same structure
            max_row = 0
            for key in request.form.keys():
                if key.startswith('donor_id_'):
                    try:
                        row_num = int(key.split('_')[-1])
                        max_row = max(max_row, row_num)
                    except ValueError:
                        continue
            
            preserved_data['max_row'] = max_row
            return render_template("batch_transactions.html", form_data=preserved_data)
        finally:
            session.close()
    
    # GET request - show empty form
    return render_template("batch_transactions.html", form_data={})

@app.route("/batch_success")
def batch_success():
    """Show batch processing success page with PDF download and mailing list options"""
    batch_num = request.args.get('batch_num', '')
    num_transactions = request.args.get('num_transactions', 0, type=int)
    total_amount = request.args.get('total_amount', 0.0, type=float)
    
    if batch_num == 'NOBATCH':
        batch_num = None
    
    session = Session()
    try:
        # Get all donor IDs from the batch that have mailing_list_status = FALSE
        if batch_num:
            # Find transactions in this batch
            batch_transactions = session.query(EagleTrustFundTransaction).filter_by(
                update_batch_num=batch_num
            ).all()
            
            # Get unique donor IDs from this batch
            donor_ids_in_batch = list(set([t.base_donor_id for t in batch_transactions]))
        else:
            # If no batch number, we can't identify specific donors
            donor_ids_in_batch = []
        
        # Find donors from this batch who have mailing_list_status = FALSE
        mailing_list_candidates = []
        if donor_ids_in_batch:
            mailing_list_candidates = session.query(EagleTrustFundDonor).filter(
                and_(
                    EagleTrustFundDonor.base_donor_id.in_(donor_ids_in_batch),
                    EagleTrustFundDonor.mailing_list_status == False
                )
            ).order_by(EagleTrustFundDonor.last_name, EagleTrustFundDonor.first_name).all()
        
        return render_template("batch_success.html",
                             batch_num=batch_num,
                             num_transactions=num_transactions,
                             total_amount=total_amount,
                             mailing_list_candidates=mailing_list_candidates)
    finally:
        session.close()

@app.route("/update_batch_mailing_list", methods=["POST"])
def update_batch_mailing_list():
    """Update mailing list status for selected donors from batch"""
    session = Session()
    try:
        # Get selected donor IDs from the form
        selected_donor_ids = request.form.getlist('donor_ids')
        
        if not selected_donor_ids:
            flash("No donors selected.", "warning")
            return redirect(request.referrer or url_for('home'))
        
        # Convert to integers
        try:
            selected_donor_ids = [int(did) for did in selected_donor_ids]
        except ValueError:
            flash("Invalid donor IDs provided.", "error")
            return redirect(request.referrer or url_for('home'))
        
        # Update mailing list status for selected donors
        updated_count = session.query(EagleTrustFundDonor).filter(
            EagleTrustFundDonor.base_donor_id.in_(selected_donor_ids)
        ).update(
            {EagleTrustFundDonor.mailing_list_status: True},
            synchronize_session='fetch'
        )
        
        session.commit()
        
        if updated_count > 0:
            flash(f"Successfully added {updated_count} donor(s) to the mailing list!", "success")
        else:
            flash("No donors were updated.", "warning")
            
        return redirect(request.referrer or url_for('home'))
        
    except Exception as e:
        session.rollback()
        app.logger.error(f"Error updating mailing list status: {e}")
        flash(f"Error updating mailing list status: {e}", "error")
        return redirect(request.referrer or url_for('home'))
    finally:
        session.close()

@app.route("/transactions", methods=["GET", "POST"])
def transaction_search():
    # Initialize empty search params for both GET and POST
    search_params = {
        'start_date': '',
        'end_date': '',
        'min_amount': '',
        'max_amount': '',
        'appeal_code': '',
        'payment_type': '',
        'update_batch_num': '',
        'bluebook_job_description': '',
        'bluebook_list_description': '',
    }

    if request.method == "POST":
        # Update search parameters from form data
        for key in search_params:
            search_params[key] = request.form.get(key, "").strip()

        session = Session()
        try:
            # Start with base query joining transactions with donors
            query = session.query(EagleTrustFundTransaction).join(
                EagleTrustFundDonor,
                EagleTrustFundTransaction.base_donor_id == EagleTrustFundDonor.base_donor_id
            )

            # Add date range conditions
            if search_params['start_date']:
                try:
                    start_date = datetime.strptime(search_params['start_date'], '%Y-%m-%d').date()
                    query = query.filter(EagleTrustFundTransaction.trans_date >= start_date)
                except ValueError:
                    flash("Invalid start date format. Please use YYYY-MM-DD.", "error")

            if search_params['end_date']:
                try:
                    end_date = datetime.strptime(search_params['end_date'], '%Y-%m-%d').date()
                    query = query.filter(EagleTrustFundTransaction.trans_date <= end_date)
                except ValueError:
                    flash("Invalid end date format. Please use YYYY-MM-DD.", "error")

            # Add amount range conditions
            if search_params['min_amount']:
                try:
                    min_amount = Decimal(search_params['min_amount'])
                    query = query.filter(EagleTrustFundTransaction.trans_amount >= min_amount)
                except InvalidOperation:
                    flash("Invalid minimum amount format.", "error")

            if search_params['max_amount']:
                try:
                    max_amount = Decimal(search_params['max_amount'])
                    query = query.filter(EagleTrustFundTransaction.trans_amount <= max_amount)
                except InvalidOperation:
                    flash("Invalid maximum amount format.", "error")

            # Add text search conditions
            if search_params['appeal_code']:
                query = query.filter(EagleTrustFundTransaction.appeal_code.ilike(f"%{search_params['appeal_code']}%"))
            if search_params['payment_type']:
                query = query.filter(EagleTrustFundTransaction.payment_type.ilike(f"%{search_params['payment_type']}%"))
            if search_params['update_batch_num']:
                query = query.filter(EagleTrustFundTransaction.update_batch_num.ilike(f"%{search_params['update_batch_num']}%"))
            if search_params['bluebook_job_description']:
                query = query.filter(EagleTrustFundTransaction.bluebook_job_description.ilike(f"%{search_params['bluebook_job_description']}%"))
            if search_params['bluebook_list_description']:
                query = query.filter(EagleTrustFundTransaction.bluebook_list_description.ilike(f"%{search_params['bluebook_list_description']}%"))

            # Get page number from request
            page = request.form.get('page', 1, type=int)
            
            # Count total results and calculate total pages
            total_results = query.count()
            total_pages = ceil(total_results / ITEMS_PER_PAGE)
            
            # Apply pagination and ordering
            transactions = query.order_by(EagleTrustFundTransaction.trans_date.desc())\
                              .offset((page - 1) * ITEMS_PER_PAGE)\
                              .limit(ITEMS_PER_PAGE)\
                              .all()

            # Calculate total amount for displayed transactions
            total_amount = sum(t.trans_amount for t in transactions)
            
            # Calculate payment type totals if searching by batch
            payment_type_totals = None
            batch_metadata = None
            is_batch_search = bool(search_params.get('update_batch_num'))
            if is_batch_search:
                # Get all transactions for this batch (not just the paginated ones)
                all_batch_transactions = query.order_by(EagleTrustFundTransaction.trans_date.desc()).all()
                payment_type_totals = {}
                
                # Collect batch metadata from first transaction
                if all_batch_transactions:
                    first_trans = all_batch_transactions[0]
                    batch_metadata = {
                        'batch_number': search_params.get('update_batch_num'),
                        'date': first_trans.trans_date.strftime('%Y-%m-%d'),
                        'payment_method': first_trans.payment_method or 'Not specified'
                    }
                
                for trans in all_batch_transactions:
                    payment_type = trans.payment_type or 'Unknown'
                    
                    # Combine N and M payment types
                    if payment_type in ['N', 'M']:
                        payment_type = 'N/M'
                    
                    if payment_type not in payment_type_totals:
                        payment_type_totals[payment_type] = {
                            'count': 0,
                            'amount': Decimal('0'),
                            'description': ''
                        }
                    payment_type_totals[payment_type]['count'] += 1
                    payment_type_totals[payment_type]['amount'] += trans.trans_amount
                    
                    # Set description based on payment type
                    if not payment_type_totals[payment_type]['description']:
                        if payment_type == 'N/M':
                            payment_type_totals[payment_type]['description'] = 'ETF/SUBS PS REPORT'
                        elif payment_type == 'G':
                            payment_type_totals[payment_type]['description'] = 'EAGLE TRUST FUND'
                        elif payment_type == 'L':
                            payment_type_totals[payment_type]['description'] = 'EFELDF (TAX-DEDUCTIBLE)'
                        elif payment_type == 'E':
                            payment_type_totals[payment_type]['description'] = 'PS EAGLES'
                        elif payment_type == 'C':
                            payment_type_totals[payment_type]['description'] = 'REG EAGLE COUNCIL'
                        elif payment_type == 'O':
                            payment_type_totals[payment_type]['description'] = 'PURCH MATERIALS EFELDF'
                        else:
                            payment_type_totals[payment_type]['description'] = trans.bluebook_job_description or 'Unknown'

            return render_template(
                "transaction_search.html",
                transactions=transactions,
                total_amount=total_amount,
                search_params=search_params,
                search_performed=True,
                current_page=page,
                total_pages=total_pages,
                total_results=total_results,
                payment_type_totals=payment_type_totals,
                is_batch_search=is_batch_search,
                batch_metadata=batch_metadata
            )

        finally:
            session.close()

    # GET request or no search parameters
    return render_template(
        "transaction_search.html",
        search_params=search_params,
        search_performed=False
    )

def format_currency(amount):
    """Helper function to format currency values"""
    if amount is None:
        return "$0.00"
    return f"${amount:,.2f}"

def generate_csv(data, headers, filename):
    """Generate CSV file from data"""
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    
    # Write headers
    writer.writerow(headers)
    
    # Write data rows - ensure all values are strings and handle long text properly
    for row in data:
        cleaned_row = []
        for cell in row:
            if cell is None:
                cleaned_row.append('')
            else:
                # Convert to string and clean up any problematic characters
                cell_str = str(cell).replace('\n', ' ').replace('\r', ' ')
                cleaned_row.append(cell_str)
        writer.writerow(cleaned_row)
    
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

def generate_pdf(data, headers, filename, title, payment_type_totals=None, batch_metadata=None):
    """Generate PDF file from data with dynamic sizing based on column count"""
    from reportlab.lib.pagesizes import letter, A4, A3, A2, A1, landscape, portrait
    
    buffer = io.BytesIO()
    
    # Determine if this is a batch search (4 columns: Donor ID, Donor Name, Type, Amount)
    is_batch_pdf = batch_metadata is not None and len(headers) == 4
    
    if is_batch_pdf:
        # Batch PDFs use portrait letter size for clean layout
        pagesize = portrait(letter)
        header_font_size = 10
        data_font_size = 9
        margin = 30
    else:
        # Determine optimal page size and font size based on number of columns for regular searches
        num_columns = len(headers)
        
        if num_columns <= 6:
            # Small tables - use letter size
            pagesize = landscape(letter)
            header_font_size = 10
            data_font_size = 8
            margin = 30
        elif num_columns <= 10:
            # Medium tables - use A4 landscape
            pagesize = landscape(A4)
            header_font_size = 9
            data_font_size = 7
            margin = 20
        elif num_columns <= 15:
            # Large tables - use A3 landscape
            pagesize = landscape(A3)
            header_font_size = 8
            data_font_size = 6
            margin = 20
        elif num_columns <= 25:
            # Very large tables - use A2 landscape
            pagesize = landscape(A2)
            header_font_size = 7
            data_font_size = 5
            margin = 15
        else:
            # Extremely large tables - use A1 landscape
            pagesize = landscape(A1)
            header_font_size = 6
            data_font_size = 4
            margin = 15
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=margin,
        leftMargin=margin,
        topMargin=margin,
        bottomMargin=margin
    )
    
    elements = []
    
    # Add title with appropriate font size
    styles = getSampleStyleSheet()
    title_style = styles['Heading1'].clone('CustomTitle')
    title_style.fontSize = max(12, header_font_size + 2)
    
    normal_style = styles['Normal'].clone('CustomNormal')
    normal_style.fontSize = max(8, data_font_size + 1)
    
    elements.append(Paragraph(title, title_style))
    
    # Add batch metadata if provided
    if batch_metadata:
        elements.append(Paragraph(f"<strong>Batch Number:</strong> {batch_metadata['batch_number']}", normal_style))
        elements.append(Paragraph(f"<strong>Transaction Date:</strong> {batch_metadata['date']}", normal_style))
        elements.append(Paragraph(f"<strong>Payment Method:</strong> {batch_metadata['payment_method']}", normal_style))
        elements.append(Paragraph("<br/>", normal_style))  # Add some space
    
    elements.append(Paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal_style))
    
    # Add payment type totals if provided
    if payment_type_totals:
        elements.append(Paragraph("<br/>", normal_style))  # Add some space
        elements.append(Paragraph("Batch Summary by Payment Type:", title_style))
        
        # Create summary table
        summary_data = [['Payment Type', 'Description', 'Transactions', 'Total Amount']]
        grand_total = Decimal('0')
        grand_count = 0
        
        for payment_type, type_data in payment_type_totals.items():
            summary_data.append([
                payment_type,
                type_data['description'],
                str(type_data['count']),
                format_currency(type_data['amount'])
            ])
            grand_total += type_data['amount']
            grand_count += type_data['count']
        
        # Add grand total row
        summary_data.append(['TOTAL', '', str(grand_count), format_currency(grand_total)])
        
        summary_table = Table(summary_data, colWidths=[0.8*inch, 2.5*inch, 1*inch, 1.2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (3, -1), 'RIGHT'),  # Right align count and amount columns
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -2), colors.white),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),  # Highlight total row
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),  # Bold total row
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ]))
        
        elements.append(summary_table)
        elements.append(Paragraph("<br/>", normal_style))  # Add space before main table
    
    # Create table with dynamic column widths
    table_data = [headers] + data
    
    # Calculate available width
    page_width = pagesize[0] - (2 * margin)
    
    if is_batch_pdf:
        # Custom column widths for batch PDFs: Donor ID, Donor Name, Type, Amount
        col_widths = [
            0.8 * inch,    # Donor ID - narrow
            3.5 * inch,    # Donor Name - wide
            0.6 * inch,    # Type - very narrow
            1.0 * inch     # Amount - medium
        ]
    else:
        # Set column widths - distribute evenly but with minimums for regular searches
        num_columns = len(headers)
        min_col_width = 0.5 * inch
        available_width = page_width - (num_columns * min_col_width)
        
        if available_width > 0:
            # Distribute extra width evenly
            col_width = min_col_width + (available_width / num_columns)
            col_widths = [col_width] * num_columns
        else:
            # Use minimum widths - will cause horizontal scrolling but prevent cutoff
            col_widths = [min_col_width] * num_columns
    
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), header_font_size),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), data_font_size),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ('WORDWRAP', (0, 0), (-1, -1), True),
    ]))
    
    elements.append(table)
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )

@app.route("/download_donor_results/<format>", methods=["POST"])
def download_donor_results(format):
    session = Session()
    try:
        # Build search query from form data
        search_params = {
            'donor_id': request.form.get("donor_id", "").strip(),
            'alternate_id': request.form.get("alternate_id", "").strip(),
            'first_name': request.form.get("first_name", "").strip(),
            'last_name': request.form.get("last_name", "").strip(),
            'email': request.form.get("email", "").strip(),
            'city': request.form.get("city", "").strip(),
            'state': request.form.get("state", "").strip(),
            'zip_code': request.form.get("zip_code", "").strip(),
            'phone': request.form.get("phone", "").strip(),
            'exclude_deceased': request.form.get("exclude_deceased"),
            'exclude_non_donors': request.form.get("exclude_non_donors"),
            'hidden_columns': request.form.get("hidden_columns", ""),
            # Status parameters
            'donor_status': request.form.get("donor_status", "").strip(),
            'newsletter_status': request.form.get("newsletter_status", "").strip(),
            # Range parameters
            'total_amount_range': request.form.get("total_amount_range", "").strip(),
            'date_added_range': request.form.get("date_added_range", "").strip(),
            'expiration_date_range': request.form.get("expiration_date_range", "").strip(),
        }
        
        query, _ = build_search_query(session, search_params)
        donors = query.all()
        
        # Get selected columns from form
        selected_columns = request.form.getlist('selected_columns')
        
        # Define all available columns with their display names
        all_columns = {
            'base_donor_id': 'Donor ID',
            'old_donor_id': 'Legacy Donor ID',
            'alternate_id': 'Alternate ID',
            'name_prefix': 'Name Prefix',
            'first_name': 'First Name',
            'last_name': 'Last Name',
            'suffix': 'Suffix',
            'formatted_full_name': 'Full Name',
            'secondary_title': 'Secondary Title',
            'secondary_first_name': 'Secondary First Name',
            'secondary_last_name': 'Secondary Last Name',
            'secondary_suffix': 'Secondary Suffix',
            'secondary_full_name': 'Secondary Full Name',
            'address_1_company': 'Company',
            'address_2_secondary': 'Address Secondary',
            'address_3_primary': 'Address Primary',
            'city': 'City',
            'state': 'State',
            'zip_plus4': 'ZIP+4',
            'phone': 'Phone',
            'work_phone': 'Work Phone',
            'cell_phone': 'Cell Phone',
            'salutation_dear': 'Salutation',
            'removal_request_note': 'Removal Request',
            'twitter': 'Twitter',
            'newsletter_status': 'Newsletter Status',
            'newsletter_status_desc': 'Newsletter Status Desc',
            'donor_status': 'Donor Status',
            'donor_status_desc': 'Donor Status Desc',
            'date_added_to_database': 'Date Added',
            'email_address': 'Email',
            'interest_borders': 'Interests - Borders',
            'interest_pro_life': 'Interests - Pro Life',
            'interest_eagle_council': 'Interests - Eagle Council',
            'interest_topic_1': 'Interest Topic 1',
            'interest_topic_2': 'Interest Topic 2',
            'interest_topic_3': 'Interest Topic 3',
            'interest_topic_4': 'Interest Topic 4',
            'education_reporter_status': 'Education Reporter Status',
            'expiration_date': 'Expiration Date',
            'news_and_notes_status': 'News Notes Status',
            'rnc_life_status': 'RNC Life Status',
            'eagle_status': 'Eagle Status',
            'eagle_state_president': 'Eagle State President',
            'flag': 'Flag',
            'changed': 'Changed',
            'interest': 'Interest',
            'house_publications': 'House Publications',
            'latest_date': 'Latest Date',
            'latest_amount': 'Latest Amount',
            'largest_date': 'Largest Date',
            'largest_amount': 'Largest Amount',
            'inception_date': 'Inception Date',
            'inception_amount': 'Inception Amount',
            'total_dollar_amount': 'Total Amount',
            'total_responses_non_zero': 'Total Responses (Non-Zero)',
            'total_responses_includes_zero': 'Total Responses'
        }
        
        # If no columns selected, use default set
        if not selected_columns:
            selected_columns = ['base_donor_id', 'first_name', 'last_name', 'email_address', 'city', 'state', 'total_dollar_amount']
        
        # Build columns list in the order selected, filtering out invalid ones
        visible_columns = [(col_id, all_columns[col_id]) for col_id in selected_columns if col_id in all_columns]
        headers = [label for _, label in visible_columns]
        
        # Prepare data rows
        data = []
        for donor in donors:
            row = []
            for col_id, _ in visible_columns:
                value = getattr(donor, col_id, None)
                if col_id in ['total_dollar_amount', 'latest_amount', 'largest_amount', 'inception_amount']:
                    value = format_currency(value)
                elif value is None:
                    value = ''
                row.append(str(value))
            data.append(row)
        
        # Generate appropriate file format
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if format == 'csv':
            return generate_csv(data, headers, f'donor_results_{timestamp}.csv')
        elif format == 'pdf':
            return generate_pdf(data, headers, f'donor_results_{timestamp}.pdf', 'Donor Search Results')
        else:
            abort(400, "Invalid format specified")
            
    finally:
        session.close()

@app.route("/download_transaction_results/<format>", methods=["POST"])
def download_transaction_results(format):
    session = Session()
    try:
        # Get search parameters
        search_params = {
            'start_date': request.form.get('start_date', '').strip(),
            'end_date': request.form.get('end_date', '').strip(),
            'min_amount': request.form.get('min_amount', '').strip(),
            'max_amount': request.form.get('max_amount', '').strip(),
            'appeal_code': request.form.get('appeal_code', '').strip(),
            'payment_type': request.form.get('payment_type', '').strip(),
            'update_batch_num': request.form.get('update_batch_num', '').strip(),
            'bluebook_job_description': request.form.get('bluebook_job_description', '').strip(),
            'bluebook_list_description': request.form.get('bluebook_list_description', '').strip(),
        }
        
        # Build query
        query = session.query(EagleTrustFundTransaction).join(
            EagleTrustFundDonor,
            EagleTrustFundTransaction.base_donor_id == EagleTrustFundDonor.base_donor_id
        )
        
        # Apply filters
        if search_params['start_date']:
            start_date = datetime.strptime(search_params['start_date'], '%Y-%m-%d').date()
            query = query.filter(EagleTrustFundTransaction.trans_date >= start_date)
        if search_params['end_date']:
            end_date = datetime.strptime(search_params['end_date'], '%Y-%m-%d').date()
            query = query.filter(EagleTrustFundTransaction.trans_date <= end_date)
        if search_params['min_amount']:
            query = query.filter(EagleTrustFundTransaction.trans_amount >= Decimal(search_params['min_amount']))
        if search_params['max_amount']:
            query = query.filter(EagleTrustFundTransaction.trans_amount <= Decimal(search_params['max_amount']))
        if search_params['appeal_code']:
            query = query.filter(EagleTrustFundTransaction.appeal_code.ilike(f"%{search_params['appeal_code']}%"))
        if search_params['payment_type']:
            query = query.filter(EagleTrustFundTransaction.payment_type.ilike(f"%{search_params['payment_type']}%"))
        if search_params['update_batch_num']:
            query = query.filter(EagleTrustFundTransaction.update_batch_num.ilike(f"%{search_params['update_batch_num']}%"))
        if search_params['bluebook_job_description']:
            query = query.filter(EagleTrustFundTransaction.bluebook_job_description.ilike(f"%{search_params['bluebook_job_description']}%"))
        if search_params['bluebook_list_description']:
            query = query.filter(EagleTrustFundTransaction.bluebook_list_description.ilike(f"%{search_params['bluebook_list_description']}%"))
        
        transactions = query.order_by(EagleTrustFundTransaction.trans_date.desc()).all()
        
        # Calculate payment type totals if searching by batch
        payment_type_totals = None
        batch_metadata = None
        is_batch_search = bool(search_params.get('update_batch_num'))
        if is_batch_search:
            payment_type_totals = {}
            
            # Collect batch metadata from first transaction
            if transactions:
                first_trans = transactions[0]
                batch_metadata = {
                    'batch_number': search_params.get('update_batch_num'),
                    'date': first_trans.trans_date.strftime('%Y-%m-%d'),
                    'payment_method': first_trans.payment_method or 'Not specified'
                }
            
            for trans in transactions:
                payment_type = trans.payment_type or 'Unknown'
                
                # Combine N and M payment types
                if payment_type in ['N', 'M']:
                    payment_type = 'N/M'
                
                if payment_type not in payment_type_totals:
                    payment_type_totals[payment_type] = {
                        'count': 0,
                        'amount': Decimal('0'),
                        'description': ''
                    }
                payment_type_totals[payment_type]['count'] += 1
                payment_type_totals[payment_type]['amount'] += trans.trans_amount
                
                # Set description based on payment type
                if not payment_type_totals[payment_type]['description']:
                    if payment_type == 'N/M':
                        payment_type_totals[payment_type]['description'] = 'ETF/SUBS PS REPORT'
                    elif payment_type == 'G':
                        payment_type_totals[payment_type]['description'] = 'EAGLE TRUST FUND'
                    elif payment_type == 'L':
                        payment_type_totals[payment_type]['description'] = 'EFELDF (TAX-DEDUCTIBLE)'
                    elif payment_type == 'E':
                        payment_type_totals[payment_type]['description'] = 'PS EAGLES'
                    elif payment_type == 'C':
                        payment_type_totals[payment_type]['description'] = 'REG EAGLE COUNCIL'
                    elif payment_type == 'O':
                        payment_type_totals[payment_type]['description'] = 'PURCH MATERIALS EFELDF'
                    else:
                        payment_type_totals[payment_type]['description'] = trans.bluebook_job_description or 'Unknown'
        
        # Define columns based on whether it's a batch search
        if is_batch_search:
            # For batch PDFs: only Donor ID, Donor Name, Type, Amount
            headers = ['Donor ID', 'Donor Name', 'Type', 'Amount']
        else:
            # For regular searches: full columns
            headers = ['Date', 'Donor Name', 'Amount', 'Payment Type', 'Payment Method', 'Batch #', 'Job Description']
        
        # Prepare transaction data rows
        transaction_data = []
        for trans in transactions:
            if is_batch_search:
                # Simplified batch format
                transaction_data.append([
                    str(trans.donor.base_donor_id),
                    trans.donor.formatted_full_name or f"{trans.donor.first_name} {trans.donor.last_name}".strip(),
                    trans.payment_type or '',
                    format_currency(trans.trans_amount)
                ])
            else:
                # Full format for non-batch searches
                job_description = trans.bluebook_job_description or ''
                if (trans.trans_date.year > 2018 and 
                    trans.payment_type == "E" and 
                    trans.bluebook_job_description == "DUES OR EAGLES"):
                    job_description = "PS EAGLES"
                
                transaction_data.append([
                    trans.trans_date.strftime('%Y-%m-%d'),
                    trans.donor.formatted_full_name or f"{trans.donor.first_name} {trans.donor.last_name}",
                    format_currency(trans.trans_amount),
                    trans.payment_type or '',
                    trans.payment_method or '',
                    trans.update_batch_num or '',
                    job_description
                ])
        
        # Generate appropriate file format
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if format == 'csv':
            return generate_csv(transaction_data, headers, f'transaction_results_{timestamp}.csv')
        elif format == 'pdf':
            title = 'Transaction Search Results'
            if is_batch_search and search_params.get('update_batch_num'):
                title = f'Batch {search_params.get("update_batch_num")} - Transaction Results'
            return generate_pdf(transaction_data, headers, f'transaction_results_{timestamp}.pdf', title, payment_type_totals, batch_metadata)
        else:
            abort(400, "Invalid format specified")
            
    finally:
        session.close()

@app.route("/refresh_transactions", methods=["GET", "POST"])
def refresh_transactions():
    if request.method == "POST":
        session = Session()
        try:
            # Get the cutoff date from form or default to 30 days ago
            cutoff_days = int(request.form.get('cutoff_days', 30))
            cutoff_date = datetime.now().date() - timedelta(days=cutoff_days)
            
            app.logger.info(f"Starting transaction refresh for transactions since {cutoff_date}")
            
            # Get all transactions since the cutoff date, grouped by donor
            recent_transactions = session.query(EagleTrustFundTransaction)\
                .filter(EagleTrustFundTransaction.trans_date >= cutoff_date)\
                .order_by(EagleTrustFundTransaction.base_donor_id, EagleTrustFundTransaction.trans_date)\
                .all()
            
            app.logger.info(f"Found {len(recent_transactions)} transactions to process")
            
            if not recent_transactions:
                flash(f"No transactions found since {cutoff_date}. Nothing to update.", "warning")
                return render_template("refresh_transactions.html", refresh_completed=False)
            
            # Group transactions by donor ID
            transactions_by_donor = {}
            for trans in recent_transactions:
                donor_id = trans.base_donor_id
                if donor_id not in transactions_by_donor:
                    transactions_by_donor[donor_id] = []
                transactions_by_donor[donor_id].append(trans)
            
            updated_donors = 0
            processed_transactions = 0
            
            # Process each donor that has recent transactions
            for donor_id, transactions in transactions_by_donor.items():
                # Get the donor record
                donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
                if not donor:
                    app.logger.warning(f"Donor {donor_id} not found, skipping transactions")
                    continue
                
                donor_updated = False
                
                # Process each transaction for this donor
                for transaction in transactions:
                    trans_date = transaction.trans_date
                    trans_amount = transaction.trans_amount
                    processed_transactions += 1
                    
                    # Update latest transaction
                    latest_date_obj = None
                    if donor.latest_date:
                        try:
                            latest_date_obj = datetime.strptime(donor.latest_date, '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    
                    if latest_date_obj is None or trans_date > latest_date_obj:
                        donor.latest_date = trans_date.strftime('%Y-%m-%d')
                        donor.latest_amount = trans_amount
                        donor_updated = True
                        
                    # Update largest transaction
                    if donor.largest_amount is None or trans_amount > donor.largest_amount:
                        donor.largest_amount = trans_amount
                        donor.largest_date = trans_date.strftime('%Y-%m-%d')
                        donor_updated = True
                    
                    # Update first/inception transaction
                    inception_date_obj = None
                    if donor.inception_date:
                        try:
                            inception_date_obj = datetime.strptime(donor.inception_date, '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    
                    if inception_date_obj is None or trans_date < inception_date_obj:
                        donor.inception_amount = trans_amount
                        donor.inception_date = trans_date.strftime('%Y-%m-%d')
                        donor_updated = True
                    
                    # Update total amounts and response counts
                    donor.total_dollar_amount = (donor.total_dollar_amount or Decimal('0')) + trans_amount
                    donor.total_responses_includes_zero = (donor.total_responses_includes_zero or 0) + 1
                    if trans_amount > 0:
                        donor.total_responses_non_zero = (donor.total_responses_non_zero or 0) + 1
                    donor_updated = True
                
                if donor_updated:
                    updated_donors += 1
                
                # Commit in batches to avoid timeouts
                if updated_donors % 50 == 0:
                    session.commit()
                    app.logger.info(f"Processed {processed_transactions} transactions, updated {updated_donors} donors")
            
            # Final commit
            session.commit()
            
            app.logger.info(f"Transaction refresh completed: {processed_transactions} transactions processed, {updated_donors} donors updated")
            flash(f"Transaction refresh completed! Processed {processed_transactions} transactions and updated {updated_donors} donors.", "success")
            return render_template("refresh_transactions.html", 
                                 refresh_completed=True,
                                 processed_count=processed_transactions,
                                 updated_count=updated_donors,
                                 cutoff_date=cutoff_date)
            
        except Exception as e:
            session.rollback()
            app.logger.error(f"Error during transaction refresh: {e}")
            flash(f"Error during transaction refresh: {e}", "error")
            return render_template("refresh_transactions.html", refresh_completed=False)
        finally:
            session.close()
    
    # GET request - show confirmation page
    return render_template("refresh_transactions.html", refresh_completed=False)

@app.route("/get_donor_info/<int:donor_id>")
def get_donor_info(donor_id):
    """AJAX endpoint to get donor information for hover tooltip"""
    session = Session()
    try:
        donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
        
        if donor:
            return jsonify({
                'found': True,
                'first_name': donor.first_name or '',
                'last_name': donor.last_name or '',
                'state': donor.state or '',
                'zip_code': donor.zip_plus4 or '',
                'city': donor.city or ''
            })
        else:
            return jsonify({'found': False})
    except Exception as e:
        app.logger.error(f"Error fetching donor info for ID {donor_id}: {e}")
        return jsonify({'found': False, 'error': str(e)})
    finally:
        session.close()

@app.route("/check_existing_batch/<batch_num>")
def check_existing_batch(batch_num):
    """AJAX endpoint to check if a batch already exists and return its summary"""
    session = Session()
    try:
        # Get all transactions for this batch
        existing_transactions = session.query(EagleTrustFundTransaction).filter_by(
            update_batch_num=batch_num
        ).all()
        
        if not existing_transactions:
            return jsonify({'exists': False})
        
        # Calculate totals
        total_count = len(existing_transactions)
        total_amount = sum(t.trans_amount for t in existing_transactions)
        
        # Calculate payment type breakdown
        payment_types = {}
        for trans in existing_transactions:
            payment_type = trans.payment_type or 'Unknown'
            
            if payment_type not in payment_types:
                payment_types[payment_type] = {
                    'count': 0,
                    'amount': 0,
                    'description': trans.bluebook_job_description or 'Unknown'
                }
            
            payment_types[payment_type]['count'] += 1
            payment_types[payment_type]['amount'] += float(trans.trans_amount)
        
        return jsonify({
            'exists': True,
            'totalCount': total_count,
            'totalAmount': float(total_amount),
            'paymentTypes': payment_types
        })
    
    except Exception as e:
        app.logger.error(f"Error checking existing batch {batch_num}: {e}")
        return jsonify({'exists': False, 'error': str(e)})
    finally:
        session.close()

@app.route("/mailing_list_generator", methods=["GET", "POST"])
def mailing_list_candidates():
    if request.method == "POST":
        session = Session()
        try:
            # Get form parameters
            query_title = request.form.get('query_title', '').strip()
            start_date_str = request.form.get('start_date', '').strip()
            end_date_str = request.form.get('end_date', '').strip()
            historical_start_date_str = request.form.get('historical_start_date', '').strip()
            exclusion_start_date_str = request.form.get('exclusion_start_date', '').strip()
            exclusion_end_date_str = request.form.get('exclusion_end_date', '').strip()
            action = request.form.get('action', 'show')

            if not query_title:
                flash("Query title is required.", "error")
                return render_template("mailing_list_generator.html")

            if not start_date_str:
                flash("Start date is required.", "error")
                return render_template("mailing_list_generator.html")

            # Parse dates with enhanced error handling
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                if end_date_str:
                    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                else:
                    end_date = datetime.now().date()

                if historical_start_date_str:
                    historical_start_date = datetime.strptime(historical_start_date_str, '%Y-%m-%d').date()
                else:
                    # Default to 3 years before start_date if not provided
                    historical_start_date = start_date - timedelta(days=365*3)

                exclusion_start_date = None
                exclusion_end_date = None
                if exclusion_start_date_str and exclusion_end_date_str:
                    exclusion_start_date = datetime.strptime(exclusion_start_date_str, '%Y-%m-%d').date()
                    exclusion_end_date = datetime.strptime(exclusion_end_date_str, '%Y-%m-%d').date()
            except ValueError:
                flash("Invalid date format. Please use YYYY-MM-DD.", "error")
                return render_template("mailing_list_generator.html")

            app.logger.info(
                f"Generating mailing list candidates '{query_title}' for date range {start_date} to {end_date}; "
                f"historical start {historical_start_date}; exclusion window {exclusion_start_date} to {exclusion_end_date}"
            )

            # Initialize tracking variables
            all_donor_ids = set()
            error_log = {
                'date_parsing_errors': [],
                'missing_transactions': [],
                'house_pub_format_issues': [],
                'data_inconsistencies': []
            }
            query_stats = {}

            # QUERY 1: Recent transactions with A/E newsletter status
            app.logger.info("Running Query 1: Recent transactions with A/E newsletter status...")
            try:
                query1_results = session.query(EagleTrustFundDonor.base_donor_id).join(
                    EagleTrustFundTransaction,
                    EagleTrustFundDonor.base_donor_id == EagleTrustFundTransaction.base_donor_id
                ).filter(
                    and_(
                        EagleTrustFundTransaction.trans_date >= start_date,
                        EagleTrustFundTransaction.trans_date <= end_date,
                        EagleTrustFundDonor.newsletter_status.in_(['A', 'E']),
                        or_(
                            EagleTrustFundDonor.donor_status.is_(None),
                            EagleTrustFundDonor.donor_status == '',
                            not_(EagleTrustFundDonor.donor_status.in_(['M', 'N']))
                        )
                    )
                ).distinct().all()

                query1_ids = {row[0] for row in query1_results}
                all_donor_ids.update(query1_ids)
                query_stats['query1'] = len(query1_ids)
            except Exception as e:
                app.logger.error(f"Error in Query 1: {e}")
                error_log['data_inconsistencies'].append(f"Query 1 error: {e}")
                query_stats['query1'] = 0

            # QUERY 2: Historical $100+ transactions (parameterized)
            app.logger.info("Running Query 2: Historical $100+ transactions...")
            try:
                historical_big_donors = session.query(EagleTrustFundDonor.base_donor_id).join(
                    EagleTrustFundTransaction,
                    EagleTrustFundDonor.base_donor_id == EagleTrustFundTransaction.base_donor_id
                ).filter(
                    and_(
                        EagleTrustFundTransaction.trans_date >= historical_start_date,
                        EagleTrustFundTransaction.trans_date <= start_date,
                        EagleTrustFundTransaction.trans_amount >= 100,
                        EagleTrustFundDonor.newsletter_status == 'A',
                        or_(
                            EagleTrustFundDonor.donor_status.is_(None),
                            EagleTrustFundDonor.donor_status == '',
                            EagleTrustFundDonor.donor_status != 'N'
                        )
                    )
                ).distinct().all()

                historical_big_donor_ids = {row[0] for row in historical_big_donors}

                # Optional exclusion window
                recent_donors_to_exclude = set()
                if exclusion_start_date and exclusion_end_date:
                    try:
                        # Method 1: compare donor.latest_date strings
                        exclude_query1 = session.query(EagleTrustFundDonor.base_donor_id).filter(
                            and_(
                                EagleTrustFundDonor.latest_date.isnot(None),
                                EagleTrustFundDonor.latest_date != '',
                                EagleTrustFundDonor.latest_date >= exclusion_start_date.strftime('%Y-%m-%d'),
                                EagleTrustFundDonor.latest_date <= exclusion_end_date.strftime('%Y-%m-%d')
                            )
                        ).all()
                        recent_donors_to_exclude.update({row[0] for row in exclude_query1})
                    except Exception as e:
                        error_log['date_parsing_errors'].append(f"Date comparison latest_date failed: {e}")

                    try:
                        from sqlalchemy import text
                        exclude_query2 = session.query(EagleTrustFundTransaction.base_donor_id).filter(
                            and_(
                                EagleTrustFundTransaction.trans_date >= exclusion_start_date,
                                EagleTrustFundTransaction.trans_date <= exclusion_end_date
                            )
                        ).group_by(EagleTrustFundTransaction.base_donor_id).having(
                            text('MAX(trans_date) >= :start AND MAX(trans_date) <= :end')
                        ).params(start=exclusion_start_date, end=exclusion_end_date).all()
                        recent_donors_to_exclude.update({row[0] for row in exclude_query2})
                    except Exception as e:
                        error_log['date_parsing_errors'].append(f"Transaction-based exclusion failed: {e}")

                # Apply exclusion
                query2_ids = historical_big_donor_ids - recent_donors_to_exclude
                all_donor_ids.update(query2_ids)
                query_stats['query2'] = len(query2_ids)
            except Exception as e:
                app.logger.error(f"Error in Query 2: {e}")
                error_log['data_inconsistencies'].append(f"Query 2 error: {e}")
                query_stats['query2'] = 0

            # QUERY 3: Lifetime donors with PS publications
            try:
                query3_results = session.query(EagleTrustFundDonor.base_donor_id).filter(
                    and_(
                        EagleTrustFundDonor.newsletter_status == 'A',
                        EagleTrustFundDonor.donor_status == 'L',
                        or_(
                            EagleTrustFundDonor.house_publications == 'PS',
                            EagleTrustFundDonor.house_publications.like('%PS %'),
                            EagleTrustFundDonor.house_publications.like('PS -%'),
                            EagleTrustFundDonor.house_publications.like('%PS - THE PHYLLIS SCHLAFLY REPORT%'),
                            EagleTrustFundDonor.house_publications.like('%, PS -%'),
                            EagleTrustFundDonor.house_publications.like('%PS(%')
                        )
                    )
                ).all()
                query3_ids = {row[0] for row in query3_results}
                all_donor_ids.update(query3_ids)
                query_stats['query3'] = len(query3_ids)
            except Exception as e:
                app.logger.error(f"Error in Query 3: {e}")
                error_log['house_pub_format_issues'].append(f"Query 3 error: {e}")
                query_stats['query3'] = 0

            # QUERY 4: Exempt donors with PS publications
            try:
                query4_results = session.query(EagleTrustFundDonor.base_donor_id).filter(
                    and_(
                        EagleTrustFundDonor.newsletter_status == 'E',
                        or_(
                            EagleTrustFundDonor.house_publications == 'PS',
                            EagleTrustFundDonor.house_publications.like('%PS %'),
                            EagleTrustFundDonor.house_publications.like('PS -%'),
                            EagleTrustFundDonor.house_publications.like('%PS - THE PHYLLIS SCHLAFLY REPORT%'),
                            EagleTrustFundDonor.house_publications.like('%, PS -%'),
                            EagleTrustFundDonor.house_publications.like('%PS(%')
                        )
                    )
                ).all()
                query4_ids = {row[0] for row in query4_results}
                all_donor_ids.update(query4_ids)
                query_stats['query4'] = len(query4_ids)
            except Exception as e:
                app.logger.error(f"Error in Query 4: {e}")
                error_log['house_pub_format_issues'].append(f"Query 4 error: {e}")
                query_stats['query4'] = 0

            # Get final donor records (candidates only: mailing_list_status = FALSE)
            final_donors_query = session.query(EagleTrustFundDonor).filter(
                and_(
                    EagleTrustFundDonor.base_donor_id.in_(all_donor_ids),
                    EagleTrustFundDonor.mailing_list_status == False,
                    or_(
                        EagleTrustFundDonor.donor_status.is_(None),
                        EagleTrustFundDonor.donor_status != 'D'
                    )
                )
            ).order_by(EagleTrustFundDonor.last_name, EagleTrustFundDonor.first_name)

            final_donors = final_donors_query.all()

            if not final_donors:
                flash("No donors found matching the specified criteria.", "warning")
                return render_template("mailing_list_generator.html")

            # Prepare CSV data (used if action == 'download')
            csv_headers = [
                'Full Name',
                'Donor ID',
                'Salutation',
                'Company/Address Line 1',
                'Address Line 2',
                'Address Line 3',
                'City',
                'State',
                'ZIP Code',
                'Latest Transaction Date'
            ]

            csv_data = []
            for donor in final_donors:
                # Build full name (fallback to formatted)
                if donor.formatted_full_name and donor.formatted_full_name.strip():
                    full_name = donor.formatted_full_name.strip()
                else:
                    full_name_parts = []
                    if donor.name_prefix:
                        full_name_parts.append(donor.name_prefix)
                    if donor.first_name:
                        full_name_parts.append(donor.first_name)
                    if donor.last_name:
                        full_name_parts.append(donor.last_name)
                    if donor.suffix:
                        full_name_parts.append(donor.suffix)
                    full_name = ' '.join(full_name_parts)

                csv_data.append([
                    full_name,
                    donor.base_donor_id,
                    donor.salutation_dear or '',
                    donor.address_1_company or '',
                    donor.address_2_secondary or '',
                    donor.address_3_primary or '',
                    donor.city or '',
                    donor.state or '',
                    donor.zip_plus4 or '',
                    donor.latest_date or ''
                ])

            if action == 'download':
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"{query_title}_candidates_{timestamp}.csv"
                return generate_csv(csv_data, csv_headers, filename)

            # Otherwise, show candidates on page with pagination
            page = request.form.get('page', 1, type=int)
            total_results = len(final_donors)
            total_pages = ceil(total_results / ITEMS_PER_PAGE) if total_results else 1
            start_index = (page - 1) * ITEMS_PER_PAGE
            end_index = start_index + ITEMS_PER_PAGE
            visible_donors = final_donors[start_index:end_index]

            # Show success message with statistics
            flash(
                f"Found {total_results} candidate(s). Recent: {query_stats.get('query1', 0)}, "
                f"Historical: {query_stats.get('query2', 0)}, Lifetime+PS: {query_stats.get('query3', 0)}, "
                f"Exempt+PS: {query_stats.get('query4', 0)}.",
                "success"
            )

            # Preserve submitted fields for the template
            submitted = {
                'query_title': query_title,
                'start_date': start_date_str,
                'end_date': end_date_str,
                'historical_start_date': historical_start_date_str,
                'exclusion_start_date': exclusion_start_date_str,
                'exclusion_end_date': exclusion_end_date_str,
            }

            return render_template(
                "mailing_list_generator.html",
                candidates=visible_donors,
                total_results=total_results,
                current_page=page,
                total_pages=total_pages,
                submitted=submitted
            )

        except Exception as e:
            app.logger.error(f"Error generating mailing list: {e}")
            flash(f"Error generating mailing list: {e}", "error")
            return render_template("mailing_list_generator.html")
        finally:
            session.close()

    # GET request - show form
    return render_template("mailing_list_generator.html")

@app.route("/generate_mailing_list", methods=["GET", "POST"])
def generate_mailing_list():
    if request.method == "POST":
        session = Session()
        try:
            # Get form parameters
            query_title = request.form.get('query_title', '').strip()
            
            if not query_title:
                flash("Query title is required.", "error")
                return render_template("generate_mailing_list.html")
            
            app.logger.info(f"Generating mailing list '{query_title}' for all active mailing list donors")
            
            # Get all donors with mailing_list_status = TRUE
            final_donors = session.query(EagleTrustFundDonor).filter(
                EagleTrustFundDonor.mailing_list_status == True
            ).order_by(EagleTrustFundDonor.last_name, EagleTrustFundDonor.first_name).all()
            
            if not final_donors:
                flash("No donors found with active mailing list status.", "warning")
                return render_template("generate_mailing_list.html")
            
            # Prepare CSV data with same format as candidates
            headers = [
                'Full Name',
                'Donor ID',
                'Salutation', 
                'Company/Address Line 1',
                'Address Line 2',
                'Address Line 3',
                'City',
                'State',
                'ZIP Code',
                'Latest Transaction Date'
            ]
            
            data = []
            for donor in final_donors:
                # Build full name with priority logic
                # Priority 1: Use formatted_full_name if it exists and has content
                if donor.formatted_full_name and donor.formatted_full_name.strip():
                    full_name = donor.formatted_full_name.strip()
                else:
                    # Priority 2: Build from individual name parts
                    full_name_parts = []
                    if donor.name_prefix:
                        full_name_parts.append(donor.name_prefix)
                    if donor.first_name:
                        full_name_parts.append(donor.first_name)
                    if donor.last_name:
                        full_name_parts.append(donor.last_name)
                    if donor.suffix:
                        full_name_parts.append(donor.suffix)
                    
                    if full_name_parts:
                        full_name = ' '.join(full_name_parts)
                    else:
                        # Priority 3: Use company name if no name information available
                        full_name = donor.address_1_company.strip() if donor.address_1_company and donor.address_1_company.strip() else ''
                
                data.append([
                    full_name,
                    donor.base_donor_id,
                    donor.salutation_dear or '',
                    donor.address_1_company or '',
                    donor.address_2_secondary or '',
                    donor.address_3_primary or '',
                    donor.city or '',
                    donor.state or '',
                    donor.zip_plus4 or '',
                    donor.latest_date or ''
                ])
            
            # Generate CSV
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{query_title}_allmail_query_{timestamp}.csv"
            
            # Show success message
            flash(f"Mailing list generated successfully! Found {len(final_donors)} active donors.", "success")
            
            return generate_csv(data, headers, filename)
            
        except Exception as e:
            app.logger.error(f"Error generating mailing list: {e}")
            flash(f"Error generating mailing list: {e}", "error")
            return render_template("generate_mailing_list.html")
        finally:
            session.close()
    
    # GET request - show form
    return render_template("generate_mailing_list.html")

@app.route("/transaction/<int:transaction_id>/edit", methods=["GET", "POST"])
def edit_transaction(transaction_id):
    session = Session()
    try:
        transaction = session.query(EagleTrustFundTransaction).filter_by(transaction_id=transaction_id).first()
        if not transaction:
            abort(404, f"Transaction #{transaction_id} not found")

        # Get the donor for this transaction
        donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=transaction.base_donor_id).first()
        if not donor:
            abort(404, f"Donor #{transaction.base_donor_id} not found")

        if request.method == "POST":
            # Store original values for summary field recalculation
            original_amount = transaction.trans_amount
            original_date = transaction.trans_date
            
            # Validate form data
            trans_date_str = request.form.get('trans_date')
            trans_amount_str = request.form.get('trans_amount', "").strip()
            
            errors = []
            trans_date = parse_date(trans_date_str)
            if not trans_date:
                errors.append("Transaction date is required and must be in YYYY-MM-DD format.")
            
            trans_amount = None
            if not trans_amount_str:
                errors.append("Transaction amount is required.")
            else:
                try:
                    trans_amount = Decimal(trans_amount_str)
                    if trans_amount < 0:
                        errors.append("Transaction amount cannot be negative.")
                except InvalidOperation:
                    errors.append("Invalid transaction amount.")
            
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("edit_transaction.html", transaction=transaction, donor=donor)

            # Update transaction fields
            transaction.trans_date = trans_date
            transaction.trans_amount = trans_amount
            transaction.appeal_code = request.form.get('appeal_code', "").strip() or None
            transaction.payment_type = request.form.get('payment_type', "").strip() or None
            transaction.update_batch_num = request.form.get('update_batch_num', "").strip() or None
            transaction.bluebook_job_description = request.form.get('bluebook_job_description', "").strip() or None
            transaction.bluebook_list_description = request.form.get('bluebook_list_description', "").strip() or None
            transaction.payment_method = request.form.get('payment_method', "").strip() or None

            # Recalculate donor summary fields by getting all transactions for this donor
            all_transactions = session.query(EagleTrustFundTransaction).filter_by(base_donor_id=donor.base_donor_id).all()
            
            if all_transactions:
                # Reset donor summary fields
                donor.total_dollar_amount = Decimal('0')
                donor.total_responses_includes_zero = 0
                donor.total_responses_non_zero = 0
                donor.latest_date = None
                donor.latest_amount = None
                donor.largest_date = None
                donor.largest_amount = None
                donor.inception_date = None
                donor.inception_amount = None
                
                # Recalculate from all transactions
                for tx in all_transactions:
                    # Update totals
                    donor.total_dollar_amount += tx.trans_amount
                    donor.total_responses_includes_zero += 1
                    if tx.trans_amount > 0:
                        donor.total_responses_non_zero += 1
                    
                    # Update latest transaction
                    latest_date_obj = None
                    if donor.latest_date:
                        try:
                            latest_date_obj = datetime.strptime(donor.latest_date, '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    
                    if latest_date_obj is None or tx.trans_date > latest_date_obj:
                        donor.latest_date = tx.trans_date.strftime('%Y-%m-%d')
                        donor.latest_amount = tx.trans_amount
                    
                    # Update largest transaction
                    if donor.largest_amount is None or tx.trans_amount > donor.largest_amount:
                        donor.largest_amount = tx.trans_amount
                        donor.largest_date = tx.trans_date.strftime('%Y-%m-%d')
                    
                    # Update inception transaction
                    inception_date_obj = None
                    if donor.inception_date:
                        try:
                            inception_date_obj = datetime.strptime(donor.inception_date, '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    
                    if inception_date_obj is None or tx.trans_date < inception_date_obj:
                        donor.inception_date = tx.trans_date.strftime('%Y-%m-%d')
                        donor.inception_amount = tx.trans_amount

            session.commit()
            flash(f"Transaction updated successfully!", "success")
            return redirect(url_for("donor", donor_id=donor.base_donor_id))

        else:
            return render_template("edit_transaction.html", transaction=transaction, donor=donor)

    except Exception as e:
        session.rollback()
        app.logger.error(f"Error editing transaction {transaction_id}: {e}")
        flash(f"Error updating transaction: {e}", "error")
        if request.method == "POST":
            return render_template("edit_transaction.html", transaction=transaction, donor=donor)
        else:
            return redirect(url_for('home'))
    finally:
        session.close()



@app.route("/quick_edit_expiration/<int:donor_id>", methods=["POST"])
def quick_edit_expiration(donor_id):
    """AJAX endpoint to quickly edit donor's expiration date"""
    session = Session()
    try:
        donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
        if not donor:
            return jsonify({'success': False, 'error': 'Donor not found'})
        
        new_expiration = request.json.get('expiration_date')
        if new_expiration:
            try:
                # Validate date format
                datetime.strptime(new_expiration, '%Y-%m-%d')
                donor.expiration_date = new_expiration
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid date format'})
        else:
            donor.expiration_date = None
        
        session.commit()
        return jsonify({'success': True, 'new_date': donor.expiration_date})
        
    except Exception as e:
        session.rollback()
        app.logger.error(f"Error updating expiration date for donor {donor_id}: {e}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        session.close()

@app.route("/quick_gift_subscription/<int:donor_id>", methods=["POST"])
def quick_gift_subscription(donor_id):
    """AJAX endpoint to gift a subscription to another donor"""
    session = Session()
    try:
        donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
        if not donor:
            return jsonify({'success': False, 'error': 'Donor not found'})
        
        recipient_id = request.json.get('recipient_id')
        if not recipient_id:
            return jsonify({'success': False, 'error': 'Recipient ID is required'})
        
        try:
            recipient_id = int(recipient_id)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid recipient ID'})
        
        # Check if recipient donor exists
        recipient = session.query(EagleTrustFundDonor).filter_by(base_donor_id=recipient_id).first()
        if not recipient:
            return jsonify({'success': False, 'error': f'Recipient donor #{recipient_id} not found'})
        
        # Update the gift relationship
        donor.gifted_to_donor_id = recipient_id
        
        # Update recipient's status to Active
        recipient.donor_status = 'A'
        recipient.donor_status_desc = 'ACTIVE'
        recipient.newsletter_status = 'A'
        recipient.newsletter_status_desc = 'ACTIVE'
        
        session.commit()
        
        recipient_name = recipient.formatted_full_name or f"{recipient.first_name or ''} {recipient.last_name or ''}".strip()
        return jsonify({
            'success': True, 
            'recipient_name': recipient_name,
            'recipient_id': recipient_id
        })
        
    except Exception as e:
        session.rollback()
        app.logger.error(f"Error creating gift subscription for donor {donor_id}: {e}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        session.close()

@app.route("/remove_gift_subscription/<int:donor_id>", methods=["POST"])
def remove_gift_subscription(donor_id):
    """AJAX endpoint to remove a gift subscription"""
    session = Session()
    try:
        donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
        if not donor:
            return jsonify({'success': False, 'error': 'Donor not found'})
        
        donor.gifted_to_donor_id = None
        session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        session.rollback()
        app.logger.error(f"Error removing gift subscription for donor {donor_id}: {e}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        session.close()

@app.route("/quick_edit_mailing_list/<int:donor_id>", methods=["POST"])
def quick_edit_mailing_list(donor_id):
    session = Session()
    try:
        data = request.get_json()
        mailing_list_status = data.get('mailing_list_status')
        mailing_until_date = data.get('mailing_until_date')
        
        # Get the donor
        donor = session.query(EagleTrustFundDonor).filter(
            EagleTrustFundDonor.base_donor_id == donor_id
        ).first()
        
        if not donor:
            return jsonify({'success': False, 'error': 'Donor not found'})
        
        # Update mailing list status
        if mailing_list_status is not None:
            donor.mailing_list_status = bool(mailing_list_status)
        
        # Update mailing until date
        if mailing_until_date is not None:
            if mailing_until_date == '':
                donor.mailing_until_date = None
            else:
                try:
                    # Parse the date string
                    parsed_date = datetime.strptime(mailing_until_date, '%Y-%m-%d').date()
                    donor.mailing_until_date = parsed_date
                except ValueError:
                    return jsonify({'success': False, 'error': 'Invalid date format'})
        
        session.commit()
        
        return jsonify({
            'success': True,
            'mailing_list_status': donor.mailing_list_status,
            'mailing_until_date': donor.mailing_until_date.strftime('%Y-%m-%d') if donor.mailing_until_date else None
        })
        
    except Exception as e:
        session.rollback()
        app.logger.error(f"Error updating mailing list status for donor {donor_id}: {e}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        session.close()

@app.route("/search_donor_by_name")
def search_donor_by_name():
    """AJAX endpoint to search for donors by name"""
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify([])
    
    session = Session()
    try:
        # Search by first name, last name, or formatted full name
        donors = session.query(EagleTrustFundDonor).filter(
            or_(
                EagleTrustFundDonor.first_name.ilike(f"%{query}%"),
                EagleTrustFundDonor.last_name.ilike(f"%{query}%"),
                EagleTrustFundDonor.formatted_full_name.ilike(f"%{query}%")
            )
        ).limit(10).all()
        
        results = []
        for donor in donors:
            name = donor.formatted_full_name or f"{donor.first_name or ''} {donor.last_name or ''}".strip()
            results.append({
                'id': donor.base_donor_id,
                'name': name,
                'city': donor.city or '',
                'state': donor.state or ''
            })
        
        return jsonify(results)
        
    finally:
        session.close()

if __name__ == "__main__":
    port = int(os.getenv("PORT") or os.getenv("FLASK_PORT", 5001))  # Check PORT first, then FLASK_PORT, default to 5001
    app.run(debug=True, port=port)
