"""Single source of truth. SQLite for local demo; PostgreSQL via DATABASE_URL for on-prem."""
import os, json, uuid, logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import create_engine, Column, String, Integer, Numeric, ForeignKey, Text, select, event, delete
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool

logger = logging.getLogger("aurora.bootstrap")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

class Base(DeclarativeBase): pass
class Customer(Base):
    __tablename__='customers'
    id=Column(String, primary_key=True); name=Column(String); email=Column(String)
class Account(Base):
    __tablename__='accounts'
    id=Column(String, primary_key=True); customer_id=Column(String, ForeignKey('customers.id')); balance=Column(Numeric(16,2)); currency=Column(String)
class Card(Base):
    __tablename__='cards'
    id=Column(String,primary_key=True); customer_id=Column(String); last4=Column(String); status=Column(String)
class Transaction(Base):
    __tablename__='transactions'
    id=Column(String,primary_key=True); customer_id=Column(String); merchant=Column(String); amount=Column(Numeric(16,2)); kind=Column(String); timestamp=Column(String); status=Column(String)
class Beneficiary(Base):
    __tablename__='beneficiaries'
    id=Column(String,primary_key=True); customer_id=Column(String); name=Column(String); pix_key=Column(String); verified=Column(Integer)
class Obligation(Base):
    __tablename__='obligations'
    id=Column(String,primary_key=True); customer_id=Column(String); label=Column(String); amount=Column(Numeric(16,2)); due=Column(String)
class Dispute(Base):
    __tablename__='disputes'
    id=Column(String,primary_key=True); customer_id=Column(String); transaction_id=Column(String); status=Column(String); created=Column(String)
class ServiceCase(Base):
    __tablename__='service_cases'
    id=Column(String,primary_key=True);customer_id=Column(String);reason=Column(Text)
    priority=Column(String);status=Column(String);assigned_to=Column(String);created=Column(String)
class Payment(Base):
    __tablename__='payments'
    id=Column(String,primary_key=True); customer_id=Column(String); beneficiary_id=Column(String); amount=Column(Numeric(16,2)); status=Column(String); idempotency_key=Column(String,unique=True); created=Column(String)
class Ledger(Base):
    __tablename__='ledger'
    id=Column(String,primary_key=True); account_id=Column(String); payment_id=Column(String); amount=Column(Numeric(16,2)); created=Column(String)
class Approval(Base):
    __tablename__='approvals'
    id=Column(String,primary_key=True); customer_id=Column(String); action=Column(String); payload=Column(Text); status=Column(String); created=Column(String)
class WorkflowSession(Base):
    __tablename__='workflow_sessions'
    id=Column(String,primary_key=True); customer_id=Column(String); state_json=Column(Text); updated=Column(String)
class Event(Base):
    __tablename__='events'
    id=Column(String,primary_key=True); customer_id=Column(String); type=Column(String); payload=Column(Text); created=Column(String)

url=os.getenv('DATABASE_URL','sqlite:///./aurora.db')
# Bound connection and lock waits make a stuck database fail with useful logs.
connect_args = {'check_same_thread': False} if url.startswith('sqlite') else {
    'connect_timeout': 10,
    'options': '-c lock_timeout=5000 -c statement_timeout=20000',
}
engine=create_engine(url,connect_args=connect_args,pool_pre_ping=True,**({'poolclass':StaticPool} if url=='sqlite://' else {}))

# Mirror PostgreSQL's foreign-key checks during local SQLite tests.
# Without this, SQLite can hide incorrect seed insertion order.
if url.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

def now(): return datetime.now(timezone.utc).isoformat()
def ident(prefix): return prefix+'-'+uuid.uuid4().hex[:10].upper()
def serialize(obj):
    if isinstance(obj,Decimal):return float(obj)
    if hasattr(obj,'__table__'):return {k:serialize(getattr(obj,k)) for k in obj.__table__.columns.keys()}
    if isinstance(obj,list):return [serialize(v) for v in obj]
    if isinstance(obj,dict):return {k:serialize(v) for k,v in obj.items()}
    return obj

def _add_seed_records(s: Session) -> None:
    """Seed entirely within the caller's transaction, with FK-safe ordering."""
    s.add(Customer(id='C001',name='Camila Rocha',email='camila@example.invalid'))
    # ForeignKey() is not an ORM relationship(): explicit flush is essential.
    s.flush()
    s.add(Account(id='ACC001',customer_id='C001',balance=Decimal('8450.00'),currency='BRL'))
    s.add(Card(id='CARD001',customer_id='C001',last4='4421',status='active'))
    for t in [
        ('TXN-ORION','Orion Digital','1280.00','card','posted'),
        ('TXN-MARKET','Mercado Verde','186.40','card','posted'),
        ('TXN-CAFÉ','Café Paulista','32.80','card','posted')]:
        s.add(Transaction(id=t[0],customer_id='C001',merchant=t[1],amount=Decimal(t[2]),kind=t[3],timestamp=now(),status=t[4]))
    s.add(Beneficiary(id='BEN001',customer_id='C001',name='Condomínio Jardim do Sol',pix_key='condominio@example.invalid',verified=1))
    # Explicitly UNVERIFIED recipient for risk / refusal scenarios. Never payable.
    s.add(Beneficiary(id='BEN003',customer_id='C001',name='Urgent Settlement Services',pix_key='urgent@example.invalid',verified=0))
    for i,(name,amount) in enumerate([('Electricity',Decimal('340')),('Groceries',Decimal('950')),('Internet and mobile',Decimal('210')),('Insurance',Decimal('600'))]):
        s.add(Obligation(id=f'OB{i+1}',customer_id='C001',label=name,amount=amount,due=(datetime.now(timezone.utc).date()+timedelta(days=5+i*3)).isoformat()))


    # Second synthetic customer for account-ownership/authorization tests.
    s.add(Customer(id='C002',name='Alex Rivera',email='alex@example.invalid'))
    s.flush()
    s.add(Account(id='ACC002',customer_id='C002',balance=Decimal('3200.00'),currency='BRL'))
    s.add(Card(id='CARD002',customer_id='C002',last4='2277',status='active'))
    s.add(Transaction(id='TXN-COFFEE-2',customer_id='C002',merchant='Cafe Rivera',
                      amount=Decimal('42.00'),kind='card',timestamp=now(),status='posted'))
    s.add(Beneficiary(id='BEN002',customer_id='C002',name='Centro Cultural Rivera',
                      pix_key='alex-center@example.invalid',verified=1))


def initialize_database() -> None:
    """Ensure the schema exists; seed a new database WITHOUT dropping tables.

    Previously the initial seed called reset(), performing DROP TABLE DDL during
    module import; that could block startup on PostgreSQL table locks.
    """
    logger.info('Initializing database schema...')
    Base.metadata.create_all(engine)
    logger.info('Database schema available; inspecting seed customer...')
    with Session(engine) as s:
        if s.scalar(select(Customer.id).limit(1)) is None:
            logger.info('New database detected; inserting seed data without DROP TABLE...')
            _add_seed_records(s)
            s.commit()
            logger.info('Demo customer C001 seeded successfully.')
        else:
            logger.info('Existing demo data detected; preserving current state.')


def reset() -> None:
    """Explicit demo-only reset, transactionally (no DROP TABLE locks).

    Never invoke this to initialize a new database. This endpoint deliberately
    deletes synthetic banking records and is disabled outside demo Compose.
    """
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        # Account must be removed before Customer (a real database foreign key).
        # Synthetic data is removed as one unit and not visible half-updated.
        for model in (WorkflowSession, Ledger, Payment, ServiceCase, Dispute, Approval, Event, Transaction,
                      Beneficiary, Obligation, Card, Account, Customer):
            s.execute(delete(model))
        s.flush()
        _add_seed_records(s)
        s.commit()
    logger.info('Synthetic demo data was reset to the baseline.')


# Called at import only for this small local demo. These INFO logs distinguish
# database bootstrap from Uvicorn's server startup and expose SQL failures.
initialize_database()
