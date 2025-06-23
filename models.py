from sqlalchemy import Column, Integer, String, Date, DECIMAL, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class EagleTrustFundDonor(Base):
    __tablename__ = "eagletrustfund_donors"

    base_donor_id               = Column(Integer, primary_key=True, autoincrement=True)
    old_donor_id                = Column(Integer)  # Legacy donor ID for reference
    alternate_id                = Column(String)
    name_prefix                 = Column(String)
    first_name                  = Column(String)
    last_name                   = Column(String)
    suffix                      = Column(String)
    formatted_full_name         = Column(String)
    secondary_title             = Column(String)
    secondary_first_name        = Column(String)
    secondary_last_name         = Column(String)
    secondary_suffix            = Column(String)
    secondary_full_name         = Column(String)
    address_1_company           = Column(String)
    address_2_secondary         = Column(String)
    address_3_primary           = Column(String)
    city                        = Column(String)
    state                       = Column(String)
    zip_plus4                   = Column(String)
    phone                       = Column(String)
    work_phone                  = Column(String)
    cell_phone                  = Column(String)
    salutation_dear             = Column(String)
    removal_request_note        = Column(String)
    twitter                     = Column(String)
    gender_code                 = Column(String)
    birth_date                  = Column(String)
    newsletter_status           = Column(String)
    newsletter_status_desc      = Column(String)
    donor_status                = Column(String)
    donor_status_desc           = Column(String)
    date_added_to_database      = Column(String)
    email_address               = Column(String)
    phone_number                = Column(String)
    interest_borders            = Column(String)
    interest_pro_life           = Column(String)
    interest_eagle_council      = Column(String)
    interest_topic_1            = Column(String)
    interest_topic_2            = Column(String)
    interest_topic_3            = Column(String)
    interest_topic_4            = Column(String)
    education_reporter_status   = Column(String)
    expiration_date             = Column(String)
    news_and_notes_status       = Column(String)
    rnc_life_status             = Column(String)
    eagle_status                = Column(String)
    eagle_state_president       = Column(String)
    flag                        = Column(String)
    changed                     = Column(String)
    interest                    = Column(String)
    house_publications          = Column(String)
    latest_date                 = Column(String)
    latest_amount               = Column(DECIMAL(10, 2))
    largest_date                = Column(String)
    largest_amount              = Column(DECIMAL(10, 2))
    inception_date              = Column(String)
    inception_amount            = Column(DECIMAL(10, 2))
    total_dollar_amount         = Column(DECIMAL(10, 2))
    total_responses_non_zero    = Column(Integer)
    total_responses_includes_zero = Column(Integer)

    # one-to-many → transactions
    transactions = relationship(
        "EagleTrustFundTransaction",
        back_populates="donor",
        order_by="EagleTrustFundTransaction.trans_date.desc()",
        lazy="select"
    )

class EagleTrustFundTransaction(Base):
    __tablename__ = "eagletrustfund_transactions"

    transaction_id            = Column(Integer, primary_key=True, autoincrement=True)
    base_donor_id             = Column(
        Integer,
        ForeignKey("eagletrustfund_donors.base_donor_id", ondelete="CASCADE"),
        nullable=False
    )
    old_donor_id              = Column(Integer)  # Legacy donor ID for reference
    trans_date                = Column(Date, nullable=False)
    trans_amount              = Column(DECIMAL(10, 2), nullable=False)
    appeal_code               = Column(String)
    payment_type              = Column(String)
    update_batch_num          = Column(String)
    bluebook_job_description  = Column(String)
    bluebook_list_description = Column(String)
    payment_method            = Column(String)

    donor = relationship(
        "EagleTrustFundDonor",
        back_populates="transactions"
    )
