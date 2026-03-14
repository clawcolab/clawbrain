#!/usr/bin/env python3
"""
ClawBrain - Enterprise AI Memory System

Features:
- 🧠 Hybrid Retrieval - Semantic + keyword + recency + importance scoring
- 📥 Conversation Ingestion - Auto-extract memories from conversations
- 🔀 Deduplication - Merge near-duplicate memories on ingest
- 🎭 Soul/Personality - 6-trait evolving personality system
- 👤 User Profile - Learns preferences and communication style
- 💭 Conversation State - Mood/intent detection
- 📊 Memory Scopes - Private, shared, team, user scoping for multi-agent
- 📝 Audit Log - Track all memory mutations for debugging
- 🗜️ Session Consolidation - Summarize and extract long-term memories
- 🔐 Encrypted Secrets - Fernet encryption for sensitive data
- ⏱️ Retention Policies - TTL, importance decay, auto-cleanup

Supports: SQLite (default), PostgreSQL, Redis
"""

__version__ = "0.3.0"
__author__ = "ClawColab"

import os
import re
import json
import math
import hashlib
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable, Union
from dataclasses import dataclass, field, asdict
from pathlib import Path
from contextlib import contextmanager
from threading import Lock
import logging

logger = logging.getLogger(__name__)


def get_bridge_script_path() -> Optional[str]:
    """
    Get the path to brain_bridge.py script.
    Used by hooks to locate the bridge script at runtime.
    
    Returns:
        Path to brain_bridge.py or None if not found
    """
    pkg_dir = Path(__file__).parent
    
    # Check multiple possible locations depending on install method
    candidates = [
        # Pip installed: clawbrain.py at dist-packages/, brain at dist-packages/brain/
        pkg_dir / "brain" / "scripts" / "brain_bridge.py",
        # Development: clawbrain.py at repo root, scripts at scripts/
        pkg_dir / "scripts" / "brain_bridge.py",
        # Legacy: if clawbrain.py is inside brain package
        pkg_dir.parent / "scripts" / "brain_bridge.py",
        pkg_dir.parent / "brain" / "scripts" / "brain_bridge.py",
    ]
    
    for c in candidates:
        if c.exists():
            return str(c)
    
    return None


# Optional dependencies
EMBEDDINGS_AVAILABLE = False
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    pass

POSTGRES_AVAILABLE = False
try:
    import psycopg2
    import psycopg2.extras
    POSTGRES_AVAILABLE = True
except ImportError:
    pass

REDIS_AVAILABLE = False
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    pass

CRYPTO_AVAILABLE = False
try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    pass


VALID_MEMORY_KINDS = ("fact", "preference", "episode", "task", "constraint", "summary", "procedure")
VALID_DURABILITIES = ("session", "short_term", "long_term")
VALID_SCOPES = ("private", "shared", "team", "user")


@dataclass
class Memory:
    id: str
    agent_id: str
    memory_type: str
    key: str
    content: str
    content_encrypted: bool
    summary: str
    keywords: List[str]
    tags: List[str]
    importance: int
    linked_to: str
    source: str
    embedding: List[float]
    created_at: str
    updated_at: str
    expires_at: Optional[str] = None
    # v0.3.0 fields
    memory_kind: str = "fact"
    confidence: float = 1.0
    durability: str = "long_term"
    scope: str = "private"
    scope_id: str = ""
    access_count: int = 0
    last_accessed_at: Optional[str] = None
    created_by_agent: str = ""


@dataclass
class ScoredMemory:
    """A memory with its retrieval score breakdown for explain mode."""
    memory: Memory
    score: float
    breakdown: Dict[str, float]
    matched_keywords: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class UserProfile:
    user_id: str
    name: Optional[str] = None
    nickname: Optional[str] = None
    preferred_name: Optional[str] = None
    communication_preferences: Dict[str, Any] = field(default_factory=dict)
    interests: List[str] = field(default_factory=list)
    expertise_areas: List[str] = field(default_factory=list)
    learning_topics: List[str] = field(default_factory=list)
    timezone: Optional[str] = None
    active_hours: Dict[str, Any] = field(default_factory=dict)
    conversation_patterns: Dict[str, Any] = field(default_factory=dict)
    emotional_patterns: Dict[str, Any] = field(default_factory=dict)
    important_dates: Dict[str, Any] = field(default_factory=dict)
    life_context: Dict[str, Any] = field(default_factory=dict)
    total_interactions: int = 0
    first_interaction: Optional[str] = None
    last_interaction: Optional[str] = None
    updated_at: Optional[str] = None


DEFAULT_CONFIG = {
    "storage_backend": os.environ.get("BRAIN_STORAGE", "auto"),  # "sqlite", "postgresql", "auto"
    "sqlite_path": os.environ.get("BRAIN_SQLITE_PATH", "./brain_data.db"),
    "postgres_host": os.environ.get("BRAIN_POSTGRES_HOST", "localhost"),
    "postgres_port": int(os.environ.get("BRAIN_POSTGRES_PORT", "5432")),
    "postgres_db": os.environ.get("BRAIN_POSTGRES_DB", "brain_db"),
    "postgres_user": os.environ.get("BRAIN_POSTGRES_USER", "brain_user"),
    "postgres_password": os.environ.get("BRAIN_POSTGRES_PASSWORD", ""),
    "redis_host": os.environ.get("BRAIN_REDIS_HOST", "localhost"),
    "redis_port": int(os.environ.get("BRAIN_REDIS_PORT", "6379")),
    "redis_db": int(os.environ.get("BRAIN_REDIS_DB", "0")),
    "redis_prefix": os.environ.get("BRAIN_REDIS_PREFIX", "brain:"),
    "embedding_model": os.environ.get("BRAIN_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
    "use_embeddings": os.environ.get("BRAIN_USE_EMBEDDINGS", "false").lower() in ("true", "1", "yes"),
    "backup_dir": os.environ.get("BRAIN_BACKUP_DIR", "./brain_backups"),
    "encryption_key": os.environ.get("BRAIN_ENCRYPTION_KEY", ""),  # Fernet key for encrypting sensitive data
}


DEFAULT_TRAITS = {
    "humor": 0.5,
    "empathy": 0.5,
    "curiosity": 0.5,
    "creativity": 0.5,
    "helpfulness": 0.5,
    "honesty": 0.5,
    "conciseness": 0.5,
    "formality": 0.5,
    "directness": 0.5,
}

# Trait decay half-life in days — unused traits drift back toward 0.5
TRAIT_DECAY_HALF_LIFE_DAYS = 30


class Brain:
    def __init__(self, config: Dict = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._lock = Lock()
        self._storage = None
        self._redis = None
        self._pg_conn = None
        self._pending_auto_migrate = False  # Flag for auto-migration
        # Embeddings are opt-in: set use_embeddings=True or BRAIN_USE_EMBEDDINGS=true to enable
        _use_emb = self.config.get("use_embeddings", False)
        self._embedder = Embedder(self.config["embedding_model"]) if (EMBEDDINGS_AVAILABLE and _use_emb) else None
        self._cipher = self._setup_encryption() if CRYPTO_AVAILABLE else None
        
        # Determine storage backend
        storage = self.config.get("storage_backend", "auto")
        
        if storage == "auto":
            if POSTGRES_AVAILABLE and self._try_postgres():
                self._setup_postgres()
                self._storage = "postgresql"
            else:
                self._setup_sqlite()
                self._storage = "sqlite"
        elif storage == "sqlite":
            self._setup_sqlite()
            self._storage = "sqlite"
        elif storage == "postgresql" and POSTGRES_AVAILABLE:
            self._setup_postgres()
            self._storage = "postgresql"
        else:
            self._setup_sqlite()
            self._storage = "sqlite"
        
        if REDIS_AVAILABLE and self.config.get("use_redis", True):
            self._setup_redis()
        
        self._backup_dir = Path(self.config["backup_dir"])
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Auto-migrate unencrypted secrets if encryption was just enabled
        if self._pending_auto_migrate and self._cipher:
            self._run_auto_migration()
        
        logger.info(f"Brain initialized with {self._storage} storage")
    
    def _try_postgres(self) -> bool:
        if not POSTGRES_AVAILABLE:
            return False
        try:
            conn = psycopg2.connect(
                host=self.config["postgres_host"],
                port=self.config["postgres_port"],
                database=self.config["postgres_db"],
                user=self.config["postgres_user"],
                password=self.config["postgres_password"],
                connect_timeout=3
            )
            conn.close()
            return True
        except Exception as e:
            logger.warning(f"PostgreSQL not available: {e}")
            return False
    
    def _setup_sqlite(self):
        db_path = Path(self.config["sqlite_path"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite_path = str(db_path)
        self._sqlite_conn = sqlite3.connect(self._sqlite_path, check_same_thread=False)
        self._sqlite_conn.row_factory = sqlite3.Row
        self._create_sqlite_tables()
        logger.info(f"SQLite initialized at {self._sqlite_path}")
    
    def _create_sqlite_tables(self):
        cursor = self._sqlite_conn.cursor()
        
        tables = [
            """CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, agent_id TEXT, session_key TEXT, messages TEXT,
                summary TEXT, keywords TEXT, embedding TEXT, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, agent_id TEXT, memory_type TEXT, key TEXT, content TEXT,
                content_encrypted INTEGER, summary TEXT, keywords TEXT, tags TEXT, importance INTEGER,
                linked_to TEXT, source TEXT, embedding TEXT, created_at TEXT, updated_at TEXT,
                expires_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY, agent_id TEXT, title TEXT, description TEXT,
                status TEXT, priority INTEGER, due_date TEXT, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS souls (
                agent_id TEXT PRIMARY KEY, traits TEXT, preferred_topics TEXT,
                interaction_count INTEGER, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS bonds (
                user_id TEXT PRIMARY KEY, level REAL, score INTEGER, total_interactions INTEGER,
                first_interaction TEXT, last_interaction TEXT, milestones TEXT,
                created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY, agent_id TEXT, title TEXT, description TEXT,
                status TEXT, progress INTEGER, milestones TEXT, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY, name TEXT, nickname TEXT, preferred_name TEXT,
                communication_preferences TEXT, interests TEXT, expertise_areas TEXT,
                learning_topics TEXT, timezone TEXT, active_hours TEXT, conversation_patterns TEXT,
                emotional_patterns TEXT, important_dates TEXT, life_context TEXT,
                total_interactions INTEGER, first_interaction TEXT, last_interaction TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS learning_insights (
                id TEXT PRIMARY KEY, insight_type TEXT, content TEXT, confidence REAL,
                source_context TEXT, times_reinforced INTEGER, times_contradicted INTEGER,
                is_active INTEGER, created_at TEXT, last_reinforced TEXT)""",
            """CREATE TABLE IF NOT EXISTS topic_clusters (
                id TEXT PRIMARY KEY, name TEXT, related_terms TEXT, parent_topic TEXT,
                child_topics TEXT, embedding TEXT, usage_count INTEGER,
                last_discussed TEXT, created_at TEXT)""",
        ]
        
        for sql in tables:
            cursor.execute(sql)

        # v0.3.0: memory_events audit log table
        cursor.execute("""CREATE TABLE IF NOT EXISTS memory_events (
            id TEXT PRIMARY KEY, memory_id TEXT, event_type TEXT, details TEXT,
            actor TEXT, created_at TEXT)""")

        # v0.3.0: per-user trait overrides — traits scoped to agent+user pairs
        cursor.execute("""CREATE TABLE IF NOT EXISTS soul_user_traits (
            agent_id TEXT, user_id TEXT, traits TEXT, interaction_count INTEGER,
            last_decay_at TEXT, created_at TEXT, updated_at TEXT,
            PRIMARY KEY (agent_id, user_id))""")

        # Migration: add last_decay_at to souls table for trait decay
        try:
            cursor.execute("ALTER TABLE souls ADD COLUMN last_decay_at TEXT")
        except sqlite3.OperationalError:
            pass

        # Migrations for existing databases: add columns if not present
        migrations = [
            "ALTER TABLE memories ADD COLUMN expires_at TEXT",
            "ALTER TABLE memories ADD COLUMN memory_kind TEXT DEFAULT 'fact'",
            "ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 1.0",
            "ALTER TABLE memories ADD COLUMN durability TEXT DEFAULT 'long_term'",
            "ALTER TABLE memories ADD COLUMN scope TEXT DEFAULT 'private'",
            "ALTER TABLE memories ADD COLUMN scope_id TEXT DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0",
            "ALTER TABLE memories ADD COLUMN last_accessed_at TEXT",
            "ALTER TABLE memories ADD COLUMN created_by_agent TEXT DEFAULT ''",
        ]
        for migration in migrations:
            try:
                cursor.execute(migration)
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Create indexes for hybrid retrieval performance
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_memories_agent_kind ON memories(agent_id, memory_kind)",
            "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, scope_id)",
            "CREATE INDEX IF NOT EXISTS idx_memories_agent_importance ON memories(agent_id, importance DESC)",
            "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_memories_durability ON memories(durability)",
            "CREATE INDEX IF NOT EXISTS idx_memory_events_memory ON memory_events(memory_id)",
            "CREATE INDEX IF NOT EXISTS idx_memory_events_type ON memory_events(event_type, created_at DESC)",
        ]
        for idx in indexes:
            try:
                cursor.execute(idx)
            except sqlite3.OperationalError:
                pass

        self._sqlite_conn.commit()
    
    def _setup_postgres(self):
        self._pg_conn = psycopg2.connect(
            host=self.config["postgres_host"],
            port=self.config["postgres_port"],
            database=self.config["postgres_db"],
            user=self.config["postgres_user"],
            password=self.config["postgres_password"]
        )
        self._pg_conn.autocommit = True
        self._create_postgres_tables()
        logger.info("PostgreSQL tables initialized")

    def _create_postgres_tables(self):
        cursor = self._pg_conn.cursor()

        tables = [
            """CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, agent_id TEXT, session_key TEXT, messages TEXT,
                summary TEXT, keywords TEXT, embedding TEXT, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, agent_id TEXT, memory_type TEXT, key TEXT, content TEXT,
                content_encrypted INTEGER, summary TEXT, keywords TEXT, tags TEXT, importance INTEGER,
                linked_to TEXT, source TEXT, embedding TEXT, created_at TEXT, updated_at TEXT,
                expires_at TEXT, memory_kind TEXT DEFAULT 'fact', confidence REAL DEFAULT 1.0,
                durability TEXT DEFAULT 'long_term', scope TEXT DEFAULT 'private',
                scope_id TEXT DEFAULT '', access_count INTEGER DEFAULT 0,
                last_accessed_at TEXT, created_by_agent TEXT DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY, agent_id TEXT, title TEXT, description TEXT,
                status TEXT, priority INTEGER, due_date TEXT, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS souls (
                agent_id TEXT PRIMARY KEY, traits TEXT, preferred_topics TEXT,
                interaction_count INTEGER, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS bonds (
                user_id TEXT PRIMARY KEY, level REAL, score INTEGER, total_interactions INTEGER,
                first_interaction TEXT, last_interaction TEXT, milestones TEXT,
                created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY, agent_id TEXT, title TEXT, description TEXT,
                status TEXT, progress INTEGER, milestones TEXT, created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY, name TEXT, nickname TEXT, preferred_name TEXT,
                communication_preferences TEXT, interests TEXT, expertise_areas TEXT,
                learning_topics TEXT, timezone TEXT, active_hours TEXT, conversation_patterns TEXT,
                emotional_patterns TEXT, important_dates TEXT, life_context TEXT,
                total_interactions INTEGER, first_interaction TEXT, last_interaction TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS learning_insights (
                id TEXT PRIMARY KEY, insight_type TEXT, content TEXT, confidence REAL,
                source_context TEXT, times_reinforced INTEGER, times_contradicted INTEGER,
                is_active INTEGER, created_at TEXT, last_reinforced TEXT)""",
            """CREATE TABLE IF NOT EXISTS topic_clusters (
                id TEXT PRIMARY KEY, name TEXT, related_terms TEXT, parent_topic TEXT,
                child_topics TEXT, embedding TEXT, usage_count INTEGER,
                last_discussed TEXT, created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS memory_events (
                id TEXT PRIMARY KEY, memory_id TEXT, event_type TEXT, details TEXT,
                actor TEXT, created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS soul_user_traits (
                agent_id TEXT, user_id TEXT, traits TEXT, interaction_count INTEGER,
                last_decay_at TEXT, created_at TEXT, updated_at TEXT,
                PRIMARY KEY (agent_id, user_id))""",
        ]

        for sql in tables:
            cursor.execute(sql)

        # Migrations for existing PG databases: add v0.3.0 columns if not present
        migrations = [
            ("memories", "expires_at", "TEXT"),
            ("memories", "memory_kind", "TEXT DEFAULT 'fact'"),
            ("memories", "confidence", "REAL DEFAULT 1.0"),
            ("memories", "durability", "TEXT DEFAULT 'long_term'"),
            ("memories", "scope", "TEXT DEFAULT 'private'"),
            ("memories", "scope_id", "TEXT DEFAULT ''"),
            ("memories", "access_count", "INTEGER DEFAULT 0"),
            ("memories", "last_accessed_at", "TEXT"),
            ("memories", "created_by_agent", "TEXT DEFAULT ''"),
            ("souls", "last_decay_at", "TEXT"),
        ]
        for table, column, col_type in migrations:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except Exception:
                # Column already exists — PG raises an error, rollback the failed statement
                self._pg_conn.rollback()

        # Create indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_memories_agent_kind ON memories(agent_id, memory_kind)",
            "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, scope_id)",
            "CREATE INDEX IF NOT EXISTS idx_memories_agent_importance ON memories(agent_id, importance DESC)",
            "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_memories_durability ON memories(durability)",
            "CREATE INDEX IF NOT EXISTS idx_memory_events_memory ON memory_events(memory_id)",
            "CREATE INDEX IF NOT EXISTS idx_memory_events_type ON memory_events(event_type, created_at DESC)",
        ]
        for idx in indexes:
            cursor.execute(idx)

        cursor.close()
    
    def _setup_redis(self):
        if not REDIS_AVAILABLE:
            return
        try:
            self._redis = redis.Redis(
                host=self.config["redis_host"],
                port=self.config["redis_port"],
                db=self.config.get("redis_db", 0),
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3
            )
            self._redis.ping()
            self._redis_prefix = self.config.get("redis_prefix", "brain:")
            logger.info("Redis connected for caching")
        except Exception as e:
            logger.warning(f"Redis not available: {e}")
            self._redis = None
    
    def _setup_encryption(self):
        """Initialize encryption cipher with key from config or environment."""
        if not CRYPTO_AVAILABLE:
            logger.warning("cryptography library not installed. Encryption unavailable.")
            return None
        
        newly_generated = False
        key = self.config.get("encryption_key", "")
        if not key:
            # Generate key file path next to database (check config since _storage not set yet)
            sqlite_path = self.config.get("sqlite_path", "")
            if sqlite_path:
                key_path = Path(sqlite_path).parent / ".brain_key"
            else:
                key_path = Path.home() / ".config" / "clawbrain" / ".brain_key"
            
            key_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Load or generate key
            if key_path.exists():
                key = key_path.read_bytes()
                logger.info(f"Loaded encryption key from {key_path}")
            else:
                key = Fernet.generate_key()
                key_path.write_bytes(key)
                key_path.chmod(0o600)  # Restrict permissions
                logger.warning(f"Generated new encryption key at {key_path}")
                logger.warning("IMPORTANT: Backup this key! Lost keys = lost encrypted data.")
                newly_generated = True
        elif isinstance(key, str):
            key = key.encode()
        
        try:
            cipher = Fernet(key)
            # Auto-migrate unencrypted secrets when key is first generated
            if newly_generated:
                self._pending_auto_migrate = True
            return cipher
        except Exception as e:
            logger.error(f"Failed to initialize encryption: {e}")
            return None
    
    def _encrypt(self, content: str) -> str:
        """Encrypt content and return base64-encoded encrypted string."""
        if not self._cipher:
            raise ValueError("Encryption not available")
        return self._cipher.encrypt(content.encode()).decode()
    
    def _decrypt(self, encrypted_content: str) -> str:
        """Decrypt base64-encoded encrypted string and return original content."""
        if not self._cipher:
            raise ValueError("Decryption not available")
        return self._cipher.decrypt(encrypted_content.encode()).decode()
    
    @property
    def storage_backend(self) -> str:
        return self._storage
    
    # ========== AUDIT LOG ==========
    def _log_event(self, memory_id: str, event_type: str, details: Dict = None, actor: str = ""):
        """Log a memory mutation event to the audit trail."""
        event_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        try:
            with self._get_cursor() as cursor:
                if self._storage == "sqlite":
                    cursor.execute(
                        """INSERT INTO memory_events (id, memory_id, event_type, details, actor, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (event_id, memory_id, event_type, json.dumps(details or {}), actor, now)
                    )
                else:
                    cursor.execute(
                        """INSERT INTO memory_events (id, memory_id, event_type, details, actor, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (event_id, memory_id, event_type, json.dumps(details or {}), actor, now)
                    )
        except Exception as e:
            logger.warning(f"Failed to log memory event: {e}")

    def get_audit_log(self, memory_id: str = None, event_type: str = None,
                      limit: int = 50, since: str = None) -> List[Dict[str, Any]]:
        """
        Retrieve audit log entries.

        Args:
            memory_id: Filter by specific memory
            event_type: Filter by event type (created, updated, merged, deleted, accessed)
            limit: Maximum entries to return
            since: ISO timestamp to filter from

        Returns:
            List of audit log entry dicts
        """
        conditions, params = [], []
        ph = "?" if self._storage == "sqlite" else "%s"

        if memory_id:
            conditions.append(f"memory_id = {ph}")
            params.append(memory_id)
        if event_type:
            conditions.append(f"event_type = {ph}")
            params.append(event_type)
        if since:
            conditions.append(f"created_at > {ph}")
            params.append(since)

        where = " AND ".join(conditions) if conditions else "1=1"

        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    f"SELECT * FROM memory_events WHERE {where} ORDER BY created_at DESC LIMIT {limit}",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"SELECT * FROM memory_events WHERE {where} ORDER BY created_at DESC LIMIT {ph}",
                    tuple(params + [limit])
                )
            rows = cursor.fetchall()

        events = []
        for row in rows:
            event = {k: row[k] for k in row.keys()}
            if isinstance(event.get("details"), str):
                try:
                    event["details"] = json.loads(event["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
            events.append(event)
        return events

    # ========== MEMORIES ==========
    def remember(self, agent_id: str, memory_type: str, content: str, key: str = None,
                 tags: List[str] = None, auto_tag: bool = False, ttl_hours: int = None,
                 memory_kind: str = "fact", confidence: float = 1.0,
                 durability: str = "long_term", scope: str = "private",
                 scope_id: str = "", deduplicate: bool = True, **kwargs) -> Memory:
        """
        Store a memory with optional tags, scoping, and deduplication.

        Args:
            agent_id: Agent identifier
            memory_type: Type of memory (e.g., "knowledge", "preference", "conversation")
            content: Memory content
            key: Optional memory key (auto-generated if not provided)
            tags: Optional list of tags for categorization
            auto_tag: If True, automatically add extracted keywords as tags
            ttl_hours: Optional time-to-live in hours (memory expires after this)
            memory_kind: Classification (fact, preference, episode, task, constraint, summary, procedure)
            confidence: Confidence score 0.0-1.0 (default 1.0)
            durability: Retention tier (session, short_term, long_term)
            scope: Visibility scope (private, shared, team, user)
            scope_id: Scope identifier (e.g., team ID, user ID)
            deduplicate: If True, check for and merge near-duplicates (default True)
            **kwargs: Additional options (importance, linked_to, source, created_by_agent)

        Returns:
            Memory object (new or merged existing)
        """
        # Validate enum fields
        if memory_kind not in VALID_MEMORY_KINDS:
            logger.warning(f"Invalid memory_kind '{memory_kind}', defaulting to 'fact'")
            memory_kind = "fact"
        if durability not in VALID_DURABILITIES:
            logger.warning(f"Invalid durability '{durability}', defaulting to 'long_term'")
            durability = "long_term"
        if scope not in VALID_SCOPES:
            logger.warning(f"Invalid scope '{scope}', defaulting to 'private'")
            scope = "private"
        confidence = max(0.0, min(1.0, confidence))

        now = datetime.now().isoformat()
        expires_at = None
        if ttl_hours is not None:
            expires_at = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()

        memory_id = hashlib.md5(f"{agent_id}:{memory_type}:{content[:100]}:{now}".encode()).hexdigest()
        keywords = self._extract_keywords([{"content": content}])
        embedding = None
        if self._embedder and self._embedder.model and memory_type != "secret":
            embedding = self._embedder.embed(content)

        # Deduplication check before storing
        if deduplicate and memory_type != "secret":
            existing = self._find_duplicate(agent_id, content, memory_kind, embedding)
            if existing:
                merged = self._merge_memory(existing, content, confidence, kwargs.get("importance", 5))
                return merged

        # Handle auto_tag: add extracted keywords as tags
        final_tags = set(tags) if tags else set()
        if auto_tag:
            for kw in keywords:
                if len(kw) > 2 and kw.lower() not in final_tags:
                    final_tags.add(kw.lower())

        # Encrypt sensitive content
        is_encrypted = False
        stored_content = content
        if memory_type == "secret" and self._cipher:
            try:
                stored_content = self._encrypt(content)
                is_encrypted = True
                embedding = None
                logger.info(f"Encrypted secret memory: {memory_id}")
            except Exception as e:
                logger.error(f"Failed to encrypt content: {e}")
                raise ValueError("Failed to encrypt sensitive content. Set BRAIN_ENCRYPTION_KEY environment variable.")
        elif memory_type == "secret" and not self._cipher:
            raise ValueError("Encryption not available. Install cryptography: pip install cryptography")

        created_by_agent = kwargs.get("created_by_agent", agent_id)
        source = kwargs.get("source", "conversation")

        memory = Memory(
            id=memory_id, agent_id=agent_id, memory_type=memory_type,
            key=key or f"{memory_type}:{content[:50]}",
            content=stored_content, content_encrypted=is_encrypted,
            summary=self._summarize([{"content": content}]) if not is_encrypted else "[Encrypted]",
            keywords=keywords if not is_encrypted else [],
            tags=list(final_tags),
            importance=kwargs.get("importance", 5),
            linked_to=kwargs.get("linked_to"), source=source,
            embedding=embedding, created_at=now, updated_at=now,
            expires_at=expires_at,
            memory_kind=memory_kind, confidence=confidence,
            durability=durability, scope=scope, scope_id=scope_id,
            access_count=0, last_accessed_at=None,
            created_by_agent=created_by_agent,
        )

        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("""INSERT OR IGNORE INTO memories
                    (id, agent_id, memory_type, key, content, content_encrypted, summary, keywords, tags,
                     importance, linked_to, source, embedding, created_at, updated_at, expires_at,
                     memory_kind, confidence, durability, scope, scope_id, access_count,
                     last_accessed_at, created_by_agent)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (memory.id, memory.agent_id, memory.memory_type, memory.key, memory.content,
                     int(memory.content_encrypted), memory.summary, json.dumps(memory.keywords),
                     json.dumps(memory.tags), memory.importance, memory.linked_to, memory.source,
                     json.dumps(memory.embedding) if memory.embedding else None,
                     memory.created_at, memory.updated_at, memory.expires_at,
                     memory.memory_kind, memory.confidence, memory.durability,
                     memory.scope, memory.scope_id, memory.access_count,
                     memory.last_accessed_at, memory.created_by_agent))
            else:
                cursor.execute("""INSERT INTO memories
                    (id, agent_id, memory_type, key, content, content_encrypted, summary, keywords, tags,
                     importance, linked_to, source, embedding, created_at, updated_at, expires_at,
                     memory_kind, confidence, durability, scope, scope_id, access_count,
                     last_accessed_at, created_by_agent)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING""",
                    (memory.id, memory.agent_id, memory.memory_type, memory.key, memory.content,
                     memory.content_encrypted, memory.summary, memory.keywords, memory.tags,
                     memory.importance, memory.linked_to, memory.source,
                     psycopg2.extras.Json(memory.embedding) if memory.embedding else None,
                     memory.created_at, memory.updated_at, memory.expires_at,
                     memory.memory_kind, memory.confidence, memory.durability,
                     memory.scope, memory.scope_id, memory.access_count,
                     memory.last_accessed_at, memory.created_by_agent))

        # Audit log
        self._log_event(memory.id, "created", {
            "memory_type": memory_type, "memory_kind": memory_kind,
            "scope": scope, "confidence": confidence, "durability": durability,
        }, actor=created_by_agent)

        return memory
    
    def recall(self, agent_id: str = None, query: str = None, memory_type: str = None,
               limit: int = 10, memory_kind: str = None, scope: str = None,
               scope_id: str = None, since: str = None, min_confidence: float = None,
               durability: str = None, weights: Dict[str, float] = None,
               explain: bool = False, include_scopes: List[str] = None,
               ) -> Union[List[Memory], List[Dict[str, Any]]]:
        """
        Hybrid retrieval with weighted scoring across multiple dimensions.

        Args:
            agent_id: Filter by agent
            query: Search query for semantic + keyword matching
            memory_type: Filter by memory_type
            limit: Max results
            memory_kind: Filter by kind (fact, preference, episode, etc.)
            scope: Filter by scope (private, shared, team, user)
            scope_id: Filter by scope_id
            since: ISO timestamp — only memories created after this time
            min_confidence: Minimum confidence threshold
            durability: Filter by durability tier
            weights: Custom scoring weights dict. Keys: semantic, keyword, recency, importance, confidence
            explain: If True, return list of ScoredMemory-like dicts with score breakdowns
            include_scopes: List of scopes to include (alternative to single scope filter)

        Returns:
            List[Memory] normally, or List[dict] with score breakdowns if explain=True
        """
        # Build SQL filter conditions
        ph = "?" if self._storage == "sqlite" else "%s"
        conditions, params = [], []

        if agent_id:
            conditions.append(f"agent_id = {ph}")
            params.append(agent_id)
        if memory_type:
            conditions.append(f"memory_type = {ph}")
            params.append(memory_type)
        if memory_kind:
            conditions.append(f"memory_kind = {ph}")
            params.append(memory_kind)
        if scope:
            conditions.append(f"scope = {ph}")
            params.append(scope)
        if scope_id is not None:
            conditions.append(f"scope_id = {ph}")
            params.append(scope_id)
        if include_scopes:
            placeholders = ",".join([ph] * len(include_scopes))
            conditions.append(f"scope IN ({placeholders})")
            params.extend(include_scopes)
        if since:
            conditions.append(f"created_at > {ph}")
            params.append(since)
        if min_confidence is not None:
            conditions.append(f"confidence >= {ph}")
            params.append(min_confidence)
        if durability:
            conditions.append(f"durability = {ph}")
            params.append(durability)

        # Always filter expired
        conditions.append(f"(expires_at IS NULL OR expires_at > {ph})")
        params.append(datetime.now().isoformat())

        where = " AND ".join(conditions) if conditions else "1=1"

        # Fetch candidates — get more than limit for scoring
        candidate_limit = max(limit * 5, 100)

        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    f"SELECT * FROM memories WHERE {where} ORDER BY importance DESC, created_at DESC LIMIT {candidate_limit}",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"SELECT * FROM memories WHERE {where} ORDER BY importance DESC, created_at DESC LIMIT {ph}",
                    tuple(params + [candidate_limit])
                )
            rows = cursor.fetchall()

        if not rows:
            return []

        candidates = [self._row_to_memory(row) for row in rows]

        # If no query, skip scoring — just return by importance/recency (original behavior)
        if not query:
            results = candidates[:limit]
            self._track_access_batch([m.id for m in results])
            if explain:
                return [{"memory": m, "score": 1.0, "breakdown": {}, "reason": "no query — sorted by importance"} for m in results]
            return results

        # Hybrid scoring
        default_weights = {
            "semantic": 0.35,
            "keyword": 0.25,
            "recency": 0.20,
            "importance": 0.10,
            "confidence": 0.10,
        }
        w = {**default_weights, **(weights or {})}

        # Pre-compute query embedding once
        query_embedding = None
        if self._embedder and self._embedder.model:
            query_embedding = self._embedder.embed(query)

        query_tokens = self._tokenize_for_search(query)

        scored = []
        for mem in candidates:
            scores = {}

            # Semantic score
            if query_embedding and mem.embedding:
                scores["semantic"] = self._cosine_similarity(query_embedding, mem.embedding)
            else:
                scores["semantic"] = 0.0

            # Keyword score
            scores["keyword"] = self._keyword_score(query_tokens, mem)

            # Recency score
            scores["recency"] = self._recency_score(mem.created_at)

            # Importance score (normalized to 0-1 from 0-10 scale)
            scores["importance"] = min(1.0, (mem.importance or 5) / 10.0)

            # Confidence score
            scores["confidence"] = mem.confidence if mem.confidence is not None else 1.0

            # Weighted final score
            final_score = sum(scores.get(k, 0) * w.get(k, 0) for k in scores)

            mem_kws = mem.keywords if isinstance(mem.keywords, list) else []
            matched_kw = [t for t in query_tokens if any(t in kw.lower() for kw in mem_kws)]

            scored.append({
                "memory": mem,
                "score": round(final_score, 4),
                "breakdown": {k: round(v, 4) for k, v in scores.items()},
                "matched_keywords": matched_kw,
                "reason": self._explain_score(scores, w, mem),
            })

        # Sort by score descending
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:limit]

        # Track access for returned memories
        self._track_access_batch([item["memory"].id for item in top])

        if explain:
            return top
        return [item["memory"] for item in top]

    # ========== HYBRID SCORING HELPERS ==========
    def _tokenize_for_search(self, text: str) -> List[str]:
        """Tokenize text for keyword matching. Lowercased, no stopwords."""
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "above", "below",
            "between", "out", "off", "over", "under", "again", "further", "then",
            "once", "here", "there", "when", "where", "why", "how", "all", "each",
            "every", "both", "few", "more", "most", "other", "some", "such", "no",
            "not", "only", "own", "same", "so", "than", "too", "very", "just",
            "because", "but", "and", "or", "if", "while", "that", "this", "what",
            "which", "who", "whom", "these", "those", "i", "me", "my", "we", "our",
            "you", "your", "he", "him", "his", "she", "her", "it", "its", "they",
            "them", "their",
        }
        tokens = re.findall(r'\b[a-z0-9]+\b', text.lower())
        return [t for t in tokens if t not in stopwords and len(t) > 1]

    def _keyword_score(self, query_tokens: List[str], memory: Memory) -> float:
        """BM25-inspired keyword overlap score between query and memory."""
        if not query_tokens:
            return 0.0

        # Build memory token set from content + keywords + tags + summary
        mem_text = " ".join([
            memory.content or "",
            " ".join(memory.keywords or []),
            " ".join(memory.tags or []),
            memory.summary or "",
            memory.key or "",
        ]).lower()
        mem_tokens = set(re.findall(r'\b[a-z0-9]+\b', mem_text))

        if not mem_tokens:
            return 0.0

        # Token overlap with term frequency weighting
        matches = sum(1 for t in query_tokens if t in mem_tokens)
        if matches == 0:
            return 0.0

        # Normalize: ratio of matched query tokens
        precision = matches / len(query_tokens)

        # Bonus for exact phrase matches in content
        query_str = " ".join(query_tokens)
        content_lower = (memory.content or "").lower()
        phrase_bonus = 0.15 if query_str in content_lower else 0.0

        return min(1.0, precision + phrase_bonus)

    def _recency_score(self, created_at: str) -> float:
        """Exponential decay recency score. Recent = higher score."""
        if not created_at:
            return 0.0
        try:
            created = datetime.fromisoformat(created_at)
            age_hours = max(0, (datetime.now() - created).total_seconds() / 3600)
            # Half-life of ~168 hours (1 week): score halves every week
            return math.exp(-0.693 * age_hours / 168)
        except (ValueError, TypeError):
            return 0.0

    def _cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return max(0.0, dot / (norm_a * norm_b))

    def _explain_score(self, scores: Dict[str, float], weights: Dict[str, float],
                       memory: Memory) -> str:
        """Generate a human-readable explanation of why this memory was scored."""
        parts = []
        dominant = max(scores, key=lambda k: scores[k] * weights.get(k, 0))

        if scores.get("semantic", 0) > 0.5:
            parts.append("strong semantic match")
        if scores.get("keyword", 0) > 0.5:
            parts.append("high keyword overlap")
        if scores.get("recency", 0) > 0.7:
            parts.append("recent memory")
        if scores.get("importance", 0) > 0.7:
            parts.append("high importance")
        if scores.get("confidence", 0) > 0.9:
            parts.append("high confidence")
        if memory.memory_kind == "preference":
            parts.append("user preference")

        if not parts:
            parts.append(f"best on {dominant}")

        return "; ".join(parts)

    def _track_access_batch(self, memory_ids: List[str]):
        """Update access_count and last_accessed_at for retrieved memories."""
        if not memory_ids:
            return
        now = datetime.now().isoformat()
        try:
            with self._get_cursor() as cursor:
                for mid in memory_ids:
                    if self._storage == "sqlite":
                        cursor.execute(
                            "UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
                            (now, mid)
                        )
                    else:
                        cursor.execute(
                            "UPDATE memories SET access_count = access_count + 1, last_accessed_at = %s WHERE id = %s",
                            (now, mid)
                        )
        except Exception as e:
            logger.warning(f"Failed to track memory access: {e}")

    # ========== DEDUPLICATION / MERGE ==========
    def _find_duplicate(self, agent_id: str, content: str, memory_kind: str,
                        embedding: List[float] = None, threshold: float = 0.92) -> Optional[Memory]:
        """
        Check if a semantically similar memory already exists.

        Uses cosine similarity on embeddings when available, falls back to
        normalized text comparison. Also checks memory_kind to avoid false merges
        across different categories.

        Args:
            agent_id: Agent to scope the search
            content: New content to check against
            memory_kind: Kind of the new memory
            embedding: Pre-computed embedding of the new content
            threshold: Similarity threshold (default 0.92)

        Returns:
            Existing Memory if duplicate found, None otherwise
        """
        # Fetch recent memories of the same kind for this agent
        ph = "?" if self._storage == "sqlite" else "%s"
        now = datetime.now().isoformat()

        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    f"""SELECT * FROM memories
                        WHERE agent_id = ? AND memory_kind = ?
                        AND (expires_at IS NULL OR expires_at > ?)
                        ORDER BY created_at DESC LIMIT 50""",
                    (agent_id, memory_kind, now)
                )
            else:
                cursor.execute(
                    f"""SELECT * FROM memories
                        WHERE agent_id = %s AND memory_kind = %s
                        AND (expires_at IS NULL OR expires_at > %s)
                        ORDER BY created_at DESC LIMIT 50""",
                    (agent_id, memory_kind, now)
                )
            rows = cursor.fetchall()

        if not rows:
            return None

        # Normalize new content for text comparison
        normalized_new = self._normalize_for_dedup(content)

        for row in rows:
            existing = self._row_to_memory(row)

            # Strategy 1: Semantic similarity (if embeddings available)
            if embedding and existing.embedding:
                sim = self._cosine_similarity(embedding, existing.embedding)
                if sim >= threshold:
                    logger.info(f"Duplicate found (semantic={sim:.3f}): {existing.id}")
                    return existing

            # Strategy 2: Normalized text comparison
            normalized_existing = self._normalize_for_dedup(existing.content)
            if normalized_new == normalized_existing:
                logger.info(f"Duplicate found (exact text match): {existing.id}")
                return existing

            # Strategy 3: High token overlap for shorter texts
            if len(normalized_new) < 200:
                overlap = self._token_overlap(normalized_new, normalized_existing)
                if overlap >= threshold:
                    logger.info(f"Duplicate found (token overlap={overlap:.3f}): {existing.id}")
                    return existing

        return None

    def _normalize_for_dedup(self, text: str) -> str:
        """Normalize text for deduplication comparison."""
        if not text:
            return ""
        # Lowercase, collapse whitespace, strip punctuation
        text = text.lower().strip()
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text

    def _token_overlap(self, text_a: str, text_b: str) -> float:
        """Compute Jaccard-like token overlap between two normalized texts."""
        tokens_a = set(text_a.split())
        tokens_b = set(text_b.split())
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union) if union else 0.0

    def _merge_memory(self, existing: Memory, new_content: str,
                      new_confidence: float, new_importance: int) -> Memory:
        """
        Merge new content into an existing memory.

        Strategy: keep existing content if it's more comprehensive,
        bump confidence and access count, update timestamp.
        """
        # Pick higher confidence and importance
        merged_confidence = max(existing.confidence or 0, new_confidence)
        merged_importance = max(existing.importance or 0, new_importance)
        now = datetime.now().isoformat()

        # If new content is substantially longer, it might be more detailed
        if len(new_content) > len(existing.content) * 1.3:
            merged_content = new_content
        else:
            merged_content = existing.content

        # Recalculate metadata for merged content
        keywords = self._extract_keywords([{"content": merged_content}])
        embedding = None
        if self._embedder and self._embedder.model and existing.memory_type != "secret":
            embedding = self._embedder.embed(merged_content)

        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    """UPDATE memories SET content = ?, confidence = ?, importance = ?,
                       access_count = access_count + 1, updated_at = ?, keywords = ?,
                       embedding = ? WHERE id = ?""",
                    (merged_content, merged_confidence, merged_importance, now,
                     json.dumps(keywords),
                     json.dumps(embedding) if embedding else None,
                     existing.id)
                )
            else:
                cursor.execute(
                    """UPDATE memories SET content = %s, confidence = %s, importance = %s,
                       access_count = access_count + 1, updated_at = %s, keywords = %s,
                       embedding = %s WHERE id = %s""",
                    (merged_content, merged_confidence, merged_importance, now,
                     keywords,
                     psycopg2.extras.Json(embedding) if embedding else None,
                     existing.id)
                )

        # Audit log
        self._log_event(existing.id, "merged", {
            "new_content_length": len(new_content),
            "kept_content": "new" if merged_content == new_content else "existing",
            "confidence": merged_confidence,
        }, actor=existing.agent_id)

        # Return updated memory
        existing.content = merged_content
        existing.confidence = merged_confidence
        existing.importance = merged_importance
        existing.updated_at = now
        existing.keywords = keywords
        existing.access_count = (existing.access_count or 0) + 1

        logger.info(f"Merged memory {existing.id}: confidence={merged_confidence}, importance={merged_importance}")
        return existing

    def forget(self, memory_id: str) -> bool:
        """
        Delete a specific memory by ID.

        Args:
            memory_id: The memory ID to delete

        Returns:
            True if memory was deleted, False if not found
        """
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            else:
                cursor.execute("DELETE FROM memories WHERE id = %s", (memory_id,))
            deleted = cursor.rowcount > 0

        if deleted:
            # Invalidate Redis cache if available
            if self._redis:
                try:
                    self._redis.delete(f"{self._redis_prefix}memory:{memory_id}")
                except Exception:
                    pass
            self._log_event(memory_id, "deleted", {}, actor="")
            logger.info(f"Deleted memory: {memory_id}")
        return deleted

    def correct(self, memory_id: str, new_content: str) -> Optional[Memory]:
        """
        Correct/update a memory's content.

        Args:
            memory_id: The memory ID to update
            new_content: The corrected content

        Returns:
            Updated Memory object, or None if not found
        """
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            else:
                cursor.execute("SELECT * FROM memories WHERE id = %s", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return None

        # Re-encrypt if the original was encrypted
        is_encrypted = bool(row["content_encrypted"])
        stored_content = new_content
        if is_encrypted and self._cipher:
            stored_content = self._encrypt(new_content)

        # Recalculate metadata
        keywords = self._extract_keywords([{"content": new_content}]) if not is_encrypted else []
        summary = self._summarize([{"content": new_content}]) if not is_encrypted else "[Encrypted]"
        embedding = None
        if self._embedder and self._embedder.model and not is_encrypted:
            embedding = self._embedder.embed(new_content)

        now = datetime.now().isoformat()
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    """UPDATE memories SET content = ?, summary = ?, keywords = ?,
                       embedding = ?, updated_at = ? WHERE id = ?""",
                    (stored_content, summary, json.dumps(keywords),
                     json.dumps(embedding) if embedding else None, now, memory_id)
                )
            else:
                cursor.execute(
                    """UPDATE memories SET content = %s, summary = %s, keywords = %s,
                       embedding = %s, updated_at = %s WHERE id = %s""",
                    (stored_content, summary, keywords,
                     psycopg2.extras.Json(embedding) if embedding else None, now, memory_id)
                )

        self._log_event(memory_id, "corrected", {
            "new_content_length": len(new_content),
        }, actor="")
        logger.info(f"Corrected memory: {memory_id}")
        # Re-fetch and return the updated memory
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            else:
                cursor.execute("SELECT * FROM memories WHERE id = %s", (memory_id,))
            row = cursor.fetchone()
        return self._row_to_memory(row) if row else None

    def cleanup_expired(self) -> int:
        """
        Delete all expired memories.

        Returns:
            Number of memories deleted
        """
        now = datetime.now().isoformat()
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                    (now,)
                )
            else:
                cursor.execute(
                    "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < %s",
                    (now,)
                )
            deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} expired memories")
        return deleted

    def decay_importance(self, agent_id: str = None, decay_factor: float = 0.95,
                         min_importance: int = 1) -> int:
        """
        Decay importance of memories over time. Call periodically to let
        old, unreinforced memories gradually fade in relevance.

        Args:
            agent_id: Optional agent filter
            decay_factor: Multiplier for importance (0.95 = 5% decay per call)
            min_importance: Minimum importance floor

        Returns:
            Number of memories affected
        """
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                if agent_id:
                    cursor.execute(
                        """UPDATE memories SET importance = MAX(?, CAST(importance * ? AS INTEGER)),
                           updated_at = ? WHERE importance > ? AND agent_id = ?""",
                        (min_importance, decay_factor, datetime.now().isoformat(), min_importance, agent_id)
                    )
                else:
                    cursor.execute(
                        """UPDATE memories SET importance = MAX(?, CAST(importance * ? AS INTEGER)),
                           updated_at = ? WHERE importance > ?""",
                        (min_importance, decay_factor, datetime.now().isoformat(), min_importance)
                    )
            else:
                if agent_id:
                    cursor.execute(
                        """UPDATE memories SET importance = GREATEST(%s, CAST(importance * %s AS INTEGER)),
                           updated_at = %s WHERE importance > %s AND agent_id = %s""",
                        (min_importance, decay_factor, datetime.now().isoformat(), min_importance, agent_id)
                    )
                else:
                    cursor.execute(
                        """UPDATE memories SET importance = GREATEST(%s, CAST(importance * %s AS INTEGER)),
                           updated_at = %s WHERE importance > %s""",
                        (min_importance, decay_factor, datetime.now().isoformat(), min_importance)
                    )
            affected = cursor.rowcount
        if affected > 0:
            logger.info(f"Decayed importance for {affected} memories (factor={decay_factor})")
        return affected

    def get_unencrypted_secrets(self) -> List[Dict]:
        """
        Find all secrets that are stored unencrypted.
        
        Returns:
            List of dicts with id, agent_id, key for unencrypted secrets
        """
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    "SELECT id, agent_id, key FROM memories WHERE memory_type = 'secret' AND content_encrypted = 0"
                )
            else:
                cursor.execute(
                    "SELECT id, agent_id, key FROM memories WHERE memory_type = 'secret' AND content_encrypted = false"
                )
            rows = cursor.fetchall()
        
        return [{"id": row["id"], "agent_id": row["agent_id"], "key": row["key"]} for row in rows]

    def migrate_secrets(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Migrate unencrypted secrets to encrypted storage.
        
        Args:
            dry_run: If True, only report what would be migrated without making changes
            
        Returns:
            Dict with migration results: {"total": N, "migrated": N, "failed": N, "errors": [...]}
        """
        if not self._cipher:
            return {
                "total": 0,
                "migrated": 0,
                "failed": 0,
                "errors": ["Encryption not available. Install cryptography: pip install cryptography"]
            }
        
        results = {"total": 0, "migrated": 0, "failed": 0, "errors": [], "dry_run": dry_run}
        
        with self._get_cursor() as cursor:
            # Find all unencrypted secrets
            if self._storage == "sqlite":
                cursor.execute(
                    "SELECT id, agent_id, key, content FROM memories WHERE memory_type = 'secret' AND content_encrypted = 0"
                )
            else:
                cursor.execute(
                    "SELECT id, agent_id, key, content FROM memories WHERE memory_type = 'secret' AND content_encrypted = false"
                )
            
            rows = cursor.fetchall()
            results["total"] = len(rows)
            
            if dry_run:
                logger.info(f"[DRY RUN] Would migrate {len(rows)} unencrypted secrets")
                return results
            
            now = datetime.now().isoformat()
            
            for row in rows:
                try:
                    # Encrypt the content
                    encrypted_content = self._encrypt(row["content"])
                    
                    # Update the record
                    if self._storage == "sqlite":
                        cursor.execute(
                            "UPDATE memories SET content = ?, content_encrypted = 1, summary = '[Encrypted]', keywords = '[]', updated_at = ? WHERE id = ?",
                            (encrypted_content, now, row["id"])
                        )
                    else:
                        cursor.execute(
                            "UPDATE memories SET content = %s, content_encrypted = true, summary = '[Encrypted]', keywords = '[]', updated_at = %s WHERE id = %s",
                            (encrypted_content, now, row["id"])
                        )
                    
                    results["migrated"] += 1
                    logger.info(f"Migrated secret: {row['id']} (key: {row['key']})")
                    
                except Exception as e:
                    results["failed"] += 1
                    error_msg = f"Failed to migrate {row['id']}: {str(e)}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)
        
        logger.info(f"Migration complete: {results['migrated']}/{results['total']} secrets encrypted")
        return results

    def _run_auto_migration(self):
        """
        Automatically migrate unencrypted secrets when encryption is first enabled.
        Called during Brain initialization when a new encryption key is generated.
        """
        try:
            unencrypted = self.get_unencrypted_secrets()
            if not unencrypted:
                logger.info("No unencrypted secrets found - nothing to migrate")
                return
            
            logger.warning(f"Found {len(unencrypted)} unencrypted secrets - auto-migrating...")
            result = self.migrate_secrets(dry_run=False)
            
            if result["migrated"] > 0:
                logger.info(f"Auto-migration complete: {result['migrated']} secrets encrypted")
            if result["failed"] > 0:
                logger.error(f"Auto-migration had {result['failed']} failures: {result['errors']}")
        except Exception as e:
            logger.error(f"Auto-migration failed: {e}")
        finally:
            self._pending_auto_migrate = False

    def search_by_tags(self, tags: List[str], agent_id: str = None, memory_type: str = None,
                       match: str = "OR", limit: int = 20) -> List[Memory]:
        """
        Search memories by tags with AND/OR logic support.

        Args:
            tags: List of tags to search for
            agent_id: Optional agent filter
            memory_type: Optional memory type filter
            match: "OR" (any tag matches) or "AND" (all tags must match)
            limit: Maximum results to return

        Returns:
            List of Memory objects matching the tags
        """
        if not tags:
            return []

        with self._get_cursor() as cursor:
            conditions, params = [], []

            if match.upper() == "OR":
                # OR logic: memory has ANY of the tags
                if self._storage == "sqlite":
                    for tag in tags:
                        conditions.append("tags LIKE ?")
                        params.append(f'%"{tag}"%')
                    where_clause = " OR ".join(conditions) if conditions else "1=0"
                else:
                    tag_conditions = []
                    for tag in tags:
                        tag_conditions.append(f"tags @> %s")
                        params.append(json.dumps([tag]))
                    where_clause = " OR ".join(tag_conditions) if tag_conditions else "1=0"
            else:
                # AND logic: memory must have ALL tags
                # For this, we check each tag and count matches
                if self._storage == "sqlite":
                    for tag in tags:
                        conditions.append("tags LIKE ?")
                        params.append(f'%"{tag}"%')
                    where_clause = " AND ".join(conditions)
                else:
                    tag_conditions = []
                    for tag in tags:
                        tag_conditions.append(f"tags @> %s")
                        params.append(json.dumps([tag]))
                    where_clause = " AND ".join(tag_conditions)

            if agent_id:
                where_clause += " AND agent_id = " + ("?" if self._storage == "sqlite" else "%s")
                params.append(agent_id)

            if memory_type:
                where_clause += " AND memory_type = " + ("?" if self._storage == "sqlite" else "%s")
                params.append(memory_type)

            if self._storage == "sqlite":
                cursor.execute(
                    f"SELECT * FROM memories WHERE {where_clause} ORDER BY importance DESC, created_at DESC LIMIT {limit}",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"SELECT * FROM memories WHERE {where_clause} ORDER BY importance DESC, created_at DESC LIMIT %s",
                    tuple(params + [limit])
                )

            rows = cursor.fetchall()

            # For AND mode on SQLite, filter results to ensure all tags present
            if match.upper() == "AND" and self._storage == "sqlite":
                filtered = []
                for row in rows:
                    mem_tags = row["tags"]
                    if isinstance(mem_tags, str):
                        mem_tags = json.loads(mem_tags) if mem_tags else []
                    if all(tag in mem_tags for tag in tags):
                        filtered.append(row)
                rows = filtered[:limit]

        return [self._row_to_memory(row) for row in rows]

    def get_all_tags(self, agent_id: str = None) -> List[str]:
        """
        Get all unique tags from memories.

        Args:
            agent_id: Optional agent filter

        Returns:
            List of unique tag strings
        """
        with self._get_cursor() as cursor:
            if agent_id:
                if self._storage == "sqlite":
                    cursor.execute("SELECT tags FROM memories WHERE agent_id = ? AND tags IS NOT NULL", (agent_id,))
                else:
                    cursor.execute("SELECT tags FROM memories WHERE agent_id = %s AND tags IS NOT NULL", (agent_id,))
            else:
                if self._storage == "sqlite":
                    cursor.execute("SELECT tags FROM memories WHERE tags IS NOT NULL")
                else:
                    cursor.execute("SELECT tags FROM memories WHERE tags IS NOT NULL")

            rows = cursor.fetchall()
            all_tags = set()
            for row in rows:
                tags = row["tags"]
                if isinstance(tags, str):
                    tags = json.loads(tags) if tags else []
                if isinstance(tags, list):
                    all_tags.update(tags)
            return sorted(list(all_tags))

    def get_tag_stats(self, agent_id: str = None, memory_type: str = None) -> Dict[str, int]:
        """
        Get tag usage statistics - which tags are most used.

        Args:
            agent_id: Optional agent filter
            memory_type: Optional memory type filter

        Returns:
            Dict mapping tag -> count, sorted by count descending
        """
        with self._get_cursor() as cursor:
            conditions, params = [], []
            if agent_id:
                conditions.append("agent_id = " + ("?" if self._storage == "sqlite" else "%s"))
                params.append(agent_id)
            if memory_type:
                conditions.append("memory_type = " + ("?" if self._storage == "sqlite" else "%s"))
                params.append(memory_type)

            where = " AND ".join(conditions) if conditions else "1=1"

            if self._storage == "sqlite":
                cursor.execute(f"SELECT tags FROM memories WHERE {where} AND tags IS NOT NULL", tuple(params))
            else:
                cursor.execute(f"SELECT tags FROM memories WHERE {where} AND tags IS NOT NULL", tuple(params))

            tag_counts = {}
            for row in cursor.fetchall():
                tags = row["tags"]
                if isinstance(tags, str):
                    tags = json.loads(tags) if tags else []
                if isinstance(tags, list):
                    for tag in tags:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1

            # Sort by count descending
            return dict(sorted(tag_counts.items(), key=lambda x: -x[1]))

    def search_by_tag_hierarchy(self, parent_tag: str, agent_id: str = None,
                                memory_type: str = None, limit: int = 20) -> List[Memory]:
        """
        Search memories by tag hierarchy (parent tag and all child tags).

        Example:
            parent_tag="api" matches: "api", "api:clawhub", "api:rest", "api/graphql"

        Args:
            parent_tag: Parent tag to match (with all children)
            agent_id: Optional agent filter
            memory_type: Optional memory type filter
            limit: Maximum results to return

        Returns:
            List of Memory objects matching the hierarchy
        """
        # Generate tag patterns for hierarchy search
        patterns = [
            f'"{parent_tag}"',           # Exact match
            f'"{parent_tag}:"',          # api:child
            f'"{parent_tag}/"',          # api/child
            f'"{parent_tag}-"',          # api-child
        ]

        with self._get_cursor() as cursor:
            conditions, params = [], []
            for pattern in patterns:
                conditions.append("tags LIKE ?")
                params.append(f'%{pattern}%')

            where_clause = " OR ".join(conditions)

            if agent_id:
                where_clause += " AND agent_id = " + ("?" if self._storage == "sqlite" else "%s")
                params.append(agent_id)

            if memory_type:
                where_clause += " AND memory_type = " + ("?" if self._storage == "sqlite" else "%s")
                params.append(memory_type)

            if self._storage == "sqlite":
                cursor.execute(
                    f"SELECT * FROM memories WHERE {where_clause} ORDER BY importance DESC, created_at DESC LIMIT {limit}",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"SELECT * FROM memories WHERE {where_clause} ORDER BY importance DESC, created_at DESC LIMIT %s",
                    tuple(params + [limit])
                )

            rows = cursor.fetchall()
        return [self._row_to_memory(row) for row in rows]

    def add_tags_to_memory(self, memory_id: str, tags: List[str]) -> bool:
        """
        Add tags to an existing memory.

        Args:
            memory_id: The memory ID to update
            tags: List of tags to add

        Returns:
            True if memory was updated, False if not found
        """
        # Get existing memory
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            else:
                cursor.execute("SELECT * FROM memories WHERE id = %s", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return False
            memory = self._row_to_memory(row)

        # Merge tags
        existing_tags = set(memory.tags)
        existing_tags.update(tags)
        updated_tags = list(existing_tags)

        # Update memory
        now = datetime.now().isoformat()
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    "UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(updated_tags), now, memory_id)
                )
            else:
                cursor.execute(
                    "UPDATE memories SET tags = %s, updated_at = %s WHERE id = %s",
                    (updated_tags, now, memory_id)
                )
        return True

    def link_memories(self, memory_id: str, linked_memory_id: str, bidirectional: bool = True) -> bool:
        """
        Link two memories together for cross-referencing.

        Args:
            memory_id: Source memory ID
            linked_memory_id: Target memory ID to link to
            bidirectional: If True, also link target back to source

        Returns:
            True if linked successfully, False if any memory not found
        """
        # Get both memories
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT id, linked_to FROM memories WHERE id IN (?, ?)", (memory_id, linked_memory_id))
            else:
                cursor.execute("SELECT id, linked_to FROM memories WHERE id IN (%s, %s)", (memory_id, linked_memory_id))

            rows = cursor.fetchall()
            found_ids = {row["id"]: row["linked_to"] for row in rows}

            if memory_id not in found_ids or linked_memory_id not in found_ids:
                return False

            # Update source memory
            current_link = found_ids.get(memory_id, "") or ""
            linked_set = set(current_link.split(",")) if current_link else set()
            linked_set.add(linked_memory_id)
            new_link = ",".join(linked_set)

            now = datetime.now().isoformat()
            if self._storage == "sqlite":
                cursor.execute("UPDATE memories SET linked_to = ?, updated_at = ? WHERE id = ?", (new_link, now, memory_id))
            else:
                cursor.execute("UPDATE memories SET linked_to = %s, updated_at = %s WHERE id = %s", (new_link, now, memory_id))

            # Update target memory (bidirectional)
            if bidirectional:
                target_link = found_ids.get(linked_memory_id, "") or ""
                target_set = set(target_link.split(",")) if target_link else set()
                target_set.add(memory_id)
                target_new_link = ",".join(target_set)

                if self._storage == "sqlite":
                    cursor.execute("UPDATE memories SET linked_to = ?, updated_at = ? WHERE id = ?",
                                   (target_new_link, now, linked_memory_id))
                else:
                    cursor.execute("UPDATE memories SET linked_to = %s, updated_at = %s WHERE id = %s",
                                   (target_new_link, now, linked_memory_id))

            return True

    def get_linked_memories(self, memory_id: str) -> List[Memory]:
        """
        Get all memories linked to a specific memory.

        Args:
            memory_id: The memory ID to get links for

        Returns:
            List of linked Memory objects
        """
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT linked_to FROM memories WHERE id = ?", (memory_id,))
            else:
                cursor.execute("SELECT linked_to FROM memories WHERE id = %s", (memory_id,))

            row = cursor.fetchone()
            if not row or not row["linked_to"]:
                return []

            linked_ids = [lid.strip() for lid in row["linked_to"].split(",") if lid.strip()]
            if not linked_ids:
                return []

            placeholders = ",".join(["?"] * len(linked_ids)) if self._storage == "sqlite" else ",".join(["%s"] * len(linked_ids))
            if self._storage == "sqlite":
                cursor.execute(f"SELECT * FROM memories WHERE id IN ({placeholders})", tuple(linked_ids))
            else:
                cursor.execute(f"SELECT * FROM memories WHERE id IN ({placeholders})", tuple(linked_ids))

            rows = cursor.fetchall()
        return [self._row_to_memory(row) for row in rows]

    def _row_to_memory(self, row) -> Memory:
        row_keys = row.keys()

        # Handle keywords - can be list (PostgreSQL) or string (SQLite)
        keywords = row["keywords"]
        if isinstance(keywords, str):
            keywords = json.loads(keywords) if keywords else []

        # Handle tags - can be list (PostgreSQL) or string (SQLite)
        tags = row["tags"] if "tags" in row_keys else []
        if isinstance(tags, str):
            tags = json.loads(tags) if tags else []

        # Handle embedding - can be list (PostgreSQL JSON) or string (SQLite)
        embedding = row["embedding"]
        if isinstance(embedding, str):
            embedding = json.loads(embedding) if embedding else None

        # Handle datetime - PostgreSQL returns datetime objects, SQLite returns strings
        def to_str(val):
            if val is None:
                return None
            if hasattr(val, 'isoformat'):
                return val.isoformat()
            return val

        created_at = to_str(row["created_at"])
        updated_at = to_str(row["updated_at"])
        expires_at = to_str(row["expires_at"]) if "expires_at" in row_keys else None
        last_accessed_at = to_str(row["last_accessed_at"]) if "last_accessed_at" in row_keys else None

        # Decrypt content if encrypted
        content = row["content"]
        is_encrypted = bool(row["content_encrypted"])
        if is_encrypted and self._cipher:
            try:
                content = self._decrypt(content)
            except Exception as e:
                logger.error(f"Failed to decrypt memory {row['id']}: {e}")
                content = "[Decryption Failed]"

        # v0.3.0 fields with safe fallbacks for pre-migration databases
        def safe_get(key, default):
            return row[key] if key in row_keys else default

        return Memory(
            id=row["id"], agent_id=row["agent_id"], memory_type=row["memory_type"],
            key=row["key"], content=content, content_encrypted=is_encrypted,
            summary=row["summary"], keywords=keywords, tags=tags,
            importance=row["importance"], linked_to=row["linked_to"], source=row["source"],
            embedding=embedding,
            created_at=created_at, updated_at=updated_at,
            expires_at=expires_at,
            memory_kind=safe_get("memory_kind", "fact"),
            confidence=safe_get("confidence", 1.0) or 1.0,
            durability=safe_get("durability", "long_term") or "long_term",
            scope=safe_get("scope", "private") or "private",
            scope_id=safe_get("scope_id", "") or "",
            access_count=safe_get("access_count", 0) or 0,
            last_accessed_at=last_accessed_at,
            created_by_agent=safe_get("created_by_agent", "") or "",
        )
    
    # ========== CONVERSATION INGESTION ==========
    def ingest_conversation(self, agent_id: str, user_id: str = "default",
                            messages: List[Dict[str, str]] = None,
                            llm_fn: Callable = None,
                            extract_types: List[str] = None,
                            session_id: str = None,
                            scope: str = "private", scope_id: str = "",
                            ) -> List[Memory]:
        """
        Extract and store memories from a conversation. Dual-mode:
        - Rule-based extraction (zero dependencies, always available)
        - LLM-powered extraction (user provides callable, higher quality)

        Args:
            agent_id: Agent identifier
            user_id: User identifier
            messages: List of message dicts with 'role' and 'content' keys
            llm_fn: Optional callable(prompt: str) -> str for LLM-powered extraction.
                     Should accept a string prompt and return a JSON string.
            extract_types: Types to extract: ["facts", "preferences", "tasks", "constraints", "episodes"]
                          Default: ["facts", "preferences", "tasks"]
            session_id: Optional session identifier for linking
            scope: Memory scope (default "private")
            scope_id: Scope identifier

        Returns:
            List of Memory objects that were stored (new or merged)
        """
        if not messages:
            return []

        extract_types = extract_types or ["facts", "preferences", "tasks"]
        stored = []

        if llm_fn:
            # LLM-powered extraction
            try:
                extracted = self._llm_extract_memories(messages, llm_fn, extract_types)
            except Exception as e:
                logger.warning(f"LLM extraction failed, falling back to rule-based: {e}")
                extracted = self._rule_based_extraction(messages, extract_types)
        else:
            # Rule-based extraction (always available)
            extracted = self._rule_based_extraction(messages, extract_types)

        for mem_data in extracted:
            memory = self.remember(
                agent_id=agent_id,
                content=mem_data["content"],
                memory_type=mem_data.get("memory_type", "learned"),
                memory_kind=mem_data.get("memory_kind", "fact"),
                confidence=mem_data.get("confidence", 0.7),
                source="conversation",
                key=mem_data.get("key"),
                importance=mem_data.get("importance", 5),
                scope=scope,
                scope_id=scope_id,
                deduplicate=True,
                auto_tag=True,
                created_by_agent=agent_id,
            )
            stored.append(memory)

        # Auto-evolve traits from conversation signals
        try:
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "human") and content:
                    # Find the next assistant response if any
                    idx = messages.index(msg)
                    response = None
                    for following in messages[idx + 1:]:
                        if following.get("role") in ("assistant", "ai", "bot"):
                            response = following.get("content", "")
                            break
                    signals = self.analyze_interaction_for_traits(content, response)
                    if signals:
                        self.evolve_traits(agent_id, signals, user_id=user_id)
        except Exception as e:
            logger.warning(f"Trait evolution during ingestion failed: {e}")

        # Update bond relationship if user_id is provided
        try:
            if user_id and user_id != "default":
                self.update_bond(user_id, agent_id, messages)
        except Exception as e:
            logger.warning(f"Bond update during ingestion failed: {e}")

        logger.info(f"Ingested conversation: {len(stored)} memories extracted from {len(messages)} messages")
        return stored

    def _extract_memories_only(self, agent_id: str, user_id: str = "default",
                               messages: List[Dict[str, str]] = None,
                               llm_fn: Callable = None,
                               extract_types: List[str] = None,
                               scope: str = "private", scope_id: str = "") -> List[Memory]:
        """
        Extract and store memories without trait evolution or bond updates.
        Used by process_turn() to avoid double-counting side effects.
        """
        if not messages:
            return []

        extract_types = extract_types or ["facts", "preferences", "tasks"]
        stored = []

        if llm_fn:
            try:
                extracted = self._llm_extract_memories(messages, llm_fn, extract_types)
            except Exception as e:
                logger.warning(f"LLM extraction failed, falling back to rule-based: {e}")
                extracted = self._rule_based_extraction(messages, extract_types)
        else:
            extracted = self._rule_based_extraction(messages, extract_types)

        for mem_data in extracted:
            memory = self.remember(
                agent_id=agent_id,
                content=mem_data["content"],
                memory_type=mem_data.get("memory_type", "learned"),
                memory_kind=mem_data.get("memory_kind", "fact"),
                confidence=mem_data.get("confidence", 0.7),
                source="conversation",
                key=mem_data.get("key"),
                importance=mem_data.get("importance", 5),
                scope=scope,
                scope_id=scope_id,
                deduplicate=True,
                auto_tag=True,
                created_by_agent=agent_id,
            )
            stored.append(memory)

        return stored

    def _rule_based_extraction(self, messages: List[Dict[str, str]],
                                extract_types: List[str]) -> List[Dict[str, Any]]:
        """
        Extract memories from conversation using pattern matching.
        No LLM required — works with pure regex/keyword rules.

        Returns list of dicts with: content, memory_kind, confidence, memory_type, key
        """
        extracted = []
        seen_contents = set()  # Avoid extracting duplicates within same batch

        # Only process user messages (these contain the information to remember)
        user_messages = [m for m in messages if m.get("role") in ("user", "human")]

        for msg in user_messages:
            content = msg.get("content", "").strip()
            if not content or len(content) < 5:
                continue

            # --- PREFERENCES ---
            if "preferences" in extract_types:
                preference_patterns = [
                    r"(?:i|I)\s+(?:prefer|like|love|enjoy|want|favor)\s+(.+?)(?:\.|$|,)",
                    r"(?:i|I)\s+(?:don'?t|do not|never)\s+(?:like|want|use|prefer)\s+(.+?)(?:\.|$|,)",
                    r"(?:i|I)'?m?\s+(?:a fan of|into|fond of|passionate about)\s+(.+?)(?:\.|$|,)",
                    r"(?:my|My)\s+(?:favorite|preferred|go-to)\s+\w+\s+(?:is|are)\s+(.+?)(?:\.|$|,)",
                    r"(?:i|I)\s+(?:always|usually|typically)\s+(.+?)(?:\.|$|,)",
                    r"(?:i|I)\s+(?:hate|dislike|can'?t stand|avoid)\s+(.+?)(?:\.|$|,)",
                ]
                for pattern in preference_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    for match in matches:
                        pref_content = match.strip()
                        if len(pref_content) > 3 and pref_content not in seen_contents:
                            # Reconstruct full preference statement for context
                            full_match = re.search(pattern, content, re.IGNORECASE)
                            if full_match:
                                full_text = full_match.group(0).strip().rstrip(".,")
                                seen_contents.add(pref_content)
                                extracted.append({
                                    "content": full_text,
                                    "memory_kind": "preference",
                                    "confidence": 0.8,
                                    "memory_type": "learned",
                                    "importance": 6,
                                    "key": f"preference:{pref_content[:40]}",
                                })

            # --- FACTS ---
            if "facts" in extract_types:
                fact_patterns = [
                    r"(?:i|I)\s+(?:work|worked)\s+(?:at|for|with)\s+(.+?)(?:\.|$|,)",
                    r"(?:i|I)'?m?\s+(?:a|an)\s+([\w\s]+?)(?:\s+at|\s+who|\.|$|,)",
                    r"(?:my|My)\s+(?:name|job|role|title|company|team)\s+is\s+(.+?)(?:\.|$|,)",
                    r"(?:i|I)\s+(?:live|lived)\s+(?:in|at|near)\s+(.+?)(?:\.|$|,)",
                    r"(?:i|I)\s+(?:use|run|have)\s+(.+?)(?:\s+for|\.|$|,)",
                    r"(?:we|We|our|Our)\s+(?:use|run|deploy|have)\s+(.+?)(?:\s+for|\.|$|,)",
                    r"(?:the|The)\s+(?:project|app|system|service|api|stack)\s+(?:uses|runs|is built with)\s+(.+?)(?:\.|$|,)",
                ]
                for pattern in fact_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    for match in matches:
                        fact_content = match.strip()
                        if len(fact_content) > 3 and fact_content not in seen_contents:
                            full_match = re.search(pattern, content, re.IGNORECASE)
                            if full_match:
                                full_text = full_match.group(0).strip().rstrip(".,")
                                seen_contents.add(fact_content)
                                extracted.append({
                                    "content": full_text,
                                    "memory_kind": "fact",
                                    "confidence": 0.75,
                                    "memory_type": "learned",
                                    "importance": 5,
                                    "key": f"fact:{fact_content[:40]}",
                                })

            # --- TASKS ---
            if "tasks" in extract_types:
                task_patterns = [
                    r"(?:remember to|don'?t forget to|make sure to|need to|todo:?|TODO:?)\s+(.+?)(?:\.|$)",
                    r"(?:can you|please|could you)\s+(?:help me|remind me to)\s+(.+?)(?:\.|$|\?)",
                    r"(?:i|I)\s+need\s+(?:to|help with)\s+(.+?)(?:\.|$)",
                    r"(?:we|We)\s+(?:should|need to|must|have to)\s+(.+?)(?:\.|$)",
                ]
                for pattern in task_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    for match in matches:
                        task_content = match.strip()
                        if len(task_content) > 5 and task_content not in seen_contents:
                            seen_contents.add(task_content)
                            extracted.append({
                                "content": task_content,
                                "memory_kind": "task",
                                "confidence": 0.85,
                                "memory_type": "learned",
                                "importance": 7,
                                "key": f"task:{task_content[:40]}",
                            })

            # --- CONSTRAINTS ---
            if "constraints" in extract_types:
                constraint_patterns = [
                    r"(?:always|never|must|don'?t ever|make sure)\s+(.+?)(?:\.|$)",
                    r"(?:important|critical|essential)(?:\s*:)?\s+(.+?)(?:\.|$)",
                    r"(?:rule|requirement|constraint)(?:\s*:)?\s+(.+?)(?:\.|$)",
                ]
                for pattern in constraint_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    for match in matches:
                        constraint_content = match.strip()
                        if len(constraint_content) > 5 and constraint_content not in seen_contents:
                            seen_contents.add(constraint_content)
                            extracted.append({
                                "content": constraint_content,
                                "memory_kind": "constraint",
                                "confidence": 0.85,
                                "memory_type": "learned",
                                "importance": 8,
                                "key": f"constraint:{constraint_content[:40]}",
                            })

        return extracted

    def _llm_extract_memories(self, messages: List[Dict[str, str]],
                               llm_fn: Callable, extract_types: List[str]) -> List[Dict[str, Any]]:
        """
        Use an LLM to extract structured memories from conversation.

        Args:
            messages: Conversation messages
            llm_fn: Callable that takes a prompt string and returns a response string
            extract_types: Types of memories to extract

        Returns:
            List of extracted memory dicts
        """
        prompt = self._build_extraction_prompt(messages, extract_types)
        response = llm_fn(prompt)

        # Parse the LLM response — expect JSON array
        try:
            # Try to extract JSON from the response
            json_match = re.search(r'\[[\s\S]*\]', response)
            if json_match:
                memories = json.loads(json_match.group())
            else:
                memories = json.loads(response)
        except json.JSONDecodeError:
            logger.warning("LLM extraction returned non-JSON response, falling back to rule-based")
            return self._rule_based_extraction(messages, extract_types)

        # Validate and normalize extracted memories
        validated = []
        for mem in memories:
            if not isinstance(mem, dict) or "content" not in mem:
                continue
            content = str(mem["content"]).strip()
            if len(content) < 5:
                continue

            kind = mem.get("memory_kind", mem.get("kind", "fact"))
            if kind not in VALID_MEMORY_KINDS:
                kind = "fact"

            validated.append({
                "content": content,
                "memory_kind": kind,
                "confidence": min(1.0, max(0.0, float(mem.get("confidence", 0.8)))),
                "memory_type": "learned",
                "importance": min(10, max(1, int(mem.get("importance", 5)))),
                "key": mem.get("key", f"{kind}:{content[:40]}"),
            })

        return validated

    def _build_extraction_prompt(self, messages: List[Dict[str, str]],
                                  extract_types: List[str]) -> str:
        """Build the extraction prompt for the LLM."""
        # Format conversation for the prompt
        conv_text = []
        for msg in messages[-30:]:  # Last 30 messages
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:500]
            conv_text.append(f"{role}: {content}")
        conversation = "\n".join(conv_text)

        type_descriptions = {
            "facts": "Facts: concrete information about the user, their work, tools, projects, environment",
            "preferences": "Preferences: things the user likes, dislikes, prefers, avoids, or cares about",
            "tasks": "Tasks: things that need to be done, reminders, action items, TODOs",
            "constraints": "Constraints: rules, requirements, things to always/never do",
            "episodes": "Episodes: notable events, problems solved, outcomes of interactions",
        }

        types_text = "\n".join(f"- {type_descriptions.get(t, t)}" for t in extract_types)

        return f"""Analyze this conversation and extract structured memories. Return a JSON array of objects.

Each object must have:
- "content": the memory text (concise but complete)
- "memory_kind": one of: fact, preference, episode, task, constraint, summary, procedure
- "confidence": 0.0-1.0 (how certain is this information)
- "importance": 1-10 (how important for future interactions)

Extract these types:
{types_text}

Rules:
- Only extract information explicitly stated or strongly implied
- Do not infer or guess beyond what's said
- Prefer concise, self-contained statements
- Set lower confidence (0.5-0.7) for implied information
- Set higher confidence (0.8-1.0) for explicitly stated information
- Do NOT extract greetings, filler, or meta-commentary

Conversation:
{conversation}

Return ONLY a JSON array, no other text:"""

    # ========== SESSION CONSOLIDATION ==========
    def consolidate_session(self, agent_id: str, user_id: str = "default",
                            messages: List[Dict[str, str]] = None,
                            session_id: str = None, llm_fn: Callable = None,
                            scope: str = "private", scope_id: str = "",
                            ) -> Dict[str, Any]:
        """
        Consolidate a conversation session into structured long-term memories.

        This is the primary end-of-session method. It:
        1. Extracts facts, preferences, tasks from the conversation
        2. Creates a session summary (episode memory)
        3. Deduplicates against existing memories
        4. Returns a consolidation report

        Args:
            agent_id: Agent identifier
            user_id: User identifier
            messages: Conversation messages to consolidate
            session_id: Optional session identifier
            llm_fn: Optional LLM callable for higher-quality extraction
            scope: Memory scope
            scope_id: Scope identifier

        Returns:
            Dict with summary, extracted memories, and consolidation stats
        """
        if not messages:
            return {"summary": "", "extracted": [], "stats": {"total": 0}}

        session_id = session_id or uuid.uuid4().hex[:12]

        # Step 1: Extract memories from conversation
        extracted = self.ingest_conversation(
            agent_id=agent_id,
            user_id=user_id,
            messages=messages,
            llm_fn=llm_fn,
            extract_types=["facts", "preferences", "tasks", "constraints"],
            session_id=session_id,
            scope=scope,
            scope_id=scope_id,
        )

        # Step 2: Generate session summary
        if llm_fn:
            try:
                summary = self._llm_summarize_session(messages, llm_fn)
            except Exception as e:
                logger.warning(f"LLM summarization failed: {e}")
                summary = self._rule_based_summary(messages)
        else:
            summary = self._rule_based_summary(messages)

        # Step 3: Store summary as an episode memory
        summary_memory = self.remember(
            agent_id=agent_id,
            content=summary,
            memory_type="episode",
            memory_kind="summary",
            durability="long_term",
            confidence=0.9,
            importance=6,
            key=f"session_{session_id}",
            scope=scope,
            scope_id=scope_id,
            deduplicate=False,  # Session summaries are unique
            created_by_agent=agent_id,
            source="consolidation",
        )

        # Step 4: Build consolidation report
        stats = {
            "total_messages": len(messages),
            "memories_extracted": len(extracted),
            "session_id": session_id,
            "memory_kinds": {},
        }
        for mem in extracted:
            kind = mem.memory_kind
            stats["memory_kinds"][kind] = stats["memory_kinds"].get(kind, 0) + 1

        report = {
            "summary": summary,
            "summary_memory_id": summary_memory.id,
            "extracted": extracted,
            "stats": stats,
        }

        logger.info(f"Consolidated session {session_id}: {len(extracted)} memories, summary stored")
        return report

    def _rule_based_summary(self, messages: List[Dict[str, str]]) -> str:
        """Generate a structured summary from conversation messages without LLM."""
        if not messages:
            return ""

        user_messages = [m.get("content", "") for m in messages if m.get("role") in ("user", "human")]
        assistant_messages = [m.get("content", "") for m in messages if m.get("role") in ("assistant", "ai")]

        # Extract key topics from user messages
        all_text = " ".join(user_messages)
        keywords = self._extract_keywords([{"content": all_text}])

        # Build structured summary
        parts = []
        parts.append(f"Session with {len(messages)} messages.")

        if keywords:
            parts.append(f"Topics discussed: {', '.join(keywords[:8])}.")

        # Count question vs statement patterns
        questions = sum(1 for m in user_messages if "?" in m)
        if questions:
            parts.append(f"User asked {questions} question(s).")

        # First and last user messages for context
        if user_messages:
            first = user_messages[0][:100]
            parts.append(f"Started with: {first}")
            if len(user_messages) > 1:
                last = user_messages[-1][:100]
                parts.append(f"Ended with: {last}")

        return " ".join(parts)

    def _llm_summarize_session(self, messages: List[Dict[str, str]],
                                llm_fn: Callable) -> str:
        """Use an LLM to generate a structured session summary."""
        conv_text = []
        for msg in messages[-30:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:500]
            conv_text.append(f"{role}: {content}")
        conversation = "\n".join(conv_text)

        prompt = f"""Summarize this conversation session in 2-4 sentences. Focus on:
- What was the main topic or task
- What was accomplished or decided
- Any unresolved items or follow-ups

Be concise and factual. Do not add opinions or speculation.

Conversation:
{conversation}

Summary:"""

        return llm_fn(prompt).strip()

    # ========== STATISTICS ==========
    def stats(self, agent_id: str = None) -> Dict[str, Any]:
        """
        Get memory system statistics.

        Args:
            agent_id: Optional filter by agent

        Returns:
            Dict with counts, breakdowns by kind/scope/durability, and health info
        """
        ph = "?" if self._storage == "sqlite" else "%s"
        conditions, params = [], []

        if agent_id:
            conditions.append(f"agent_id = {ph}")
            params.append(agent_id)

        # Filter expired
        conditions.append(f"(expires_at IS NULL OR expires_at > {ph})")
        params.append(datetime.now().isoformat())

        where = " AND ".join(conditions) if conditions else "1=1"

        result = {
            "total_memories": 0,
            "by_kind": {},
            "by_scope": {},
            "by_durability": {},
            "by_memory_type": {},
            "avg_importance": 0,
            "avg_confidence": 0,
            "total_access_count": 0,
            "storage_backend": self._storage,
            "encryption_enabled": self._cipher is not None,
            "embeddings_enabled": self._embedder is not None and self._embedder.model is not None,
        }

        with self._get_cursor() as cursor:
            # Total count
            if self._storage == "sqlite":
                cursor.execute(f"SELECT COUNT(*) as cnt FROM memories WHERE {where}", tuple(params))
            else:
                cursor.execute(f"SELECT COUNT(*) as cnt FROM memories WHERE {where}", tuple(params))
            row = cursor.fetchone()
            result["total_memories"] = row["cnt"] if row else 0

            if result["total_memories"] == 0:
                return result

            # Aggregates
            if self._storage == "sqlite":
                cursor.execute(
                    f"""SELECT AVG(importance) as avg_imp, AVG(confidence) as avg_conf,
                        SUM(access_count) as total_acc FROM memories WHERE {where}""",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"""SELECT AVG(importance) as avg_imp, AVG(confidence) as avg_conf,
                        SUM(access_count) as total_acc FROM memories WHERE {where}""",
                    tuple(params)
                )
            agg = cursor.fetchone()
            if agg:
                result["avg_importance"] = round(float(agg["avg_imp"] or 0), 2)
                result["avg_confidence"] = round(float(agg["avg_conf"] or 0), 2)
                result["total_access_count"] = int(agg["total_acc"] or 0)

            # Breakdown by memory_kind
            if self._storage == "sqlite":
                cursor.execute(
                    f"SELECT memory_kind, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY memory_kind",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"SELECT memory_kind, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY memory_kind",
                    tuple(params)
                )
            for row in cursor.fetchall():
                result["by_kind"][row["memory_kind"] or "unknown"] = row["cnt"]

            # Breakdown by scope
            if self._storage == "sqlite":
                cursor.execute(
                    f"SELECT scope, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY scope",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"SELECT scope, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY scope",
                    tuple(params)
                )
            for row in cursor.fetchall():
                result["by_scope"][row["scope"] or "private"] = row["cnt"]

            # Breakdown by durability
            if self._storage == "sqlite":
                cursor.execute(
                    f"SELECT durability, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY durability",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"SELECT durability, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY durability",
                    tuple(params)
                )
            for row in cursor.fetchall():
                result["by_durability"][row["durability"] or "long_term"] = row["cnt"]

            # Breakdown by memory_type
            if self._storage == "sqlite":
                cursor.execute(
                    f"SELECT memory_type, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY memory_type",
                    tuple(params)
                )
            else:
                cursor.execute(
                    f"SELECT memory_type, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY memory_type",
                    tuple(params)
                )
            for row in cursor.fetchall():
                result["by_memory_type"][row["memory_type"] or "unknown"] = row["cnt"]

            # Audit log stats
            try:
                cursor.execute("SELECT COUNT(*) as cnt FROM memory_events")
                evt_row = cursor.fetchone()
                result["total_audit_events"] = evt_row["cnt"] if evt_row else 0
            except Exception:
                result["total_audit_events"] = 0

        return result

    # ========== CONVERSATIONS ==========
    def remember_conversation(self, session_key: str, messages: List[Dict], agent_id: str = "assistant", summary: str = None) -> str:
        now = datetime.now().isoformat()
        conv_id = str(hashlib.md5(f"{session_key}:{now}".encode()).hexdigest())
        keywords = self._extract_keywords(messages)
        
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("""INSERT OR REPLACE INTO conversations 
                    (id, agent_id, session_key, messages, summary, keywords, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (conv_id, agent_id, session_key, json.dumps(messages), summary,
                     json.dumps(keywords), now, now))
            else:
                cursor.execute("""INSERT INTO conversations 
                    (id, agent_id, session_key, messages, summary, keywords, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET messages = conversations.messages || EXCLUDED.messages""",
                    (conv_id, agent_id, session_key, json.dumps(messages), summary,
                     json.dumps(keywords), now, now))
        return conv_id
    
    def get_conversation(self, session_key: str, limit: int = None) -> List[Dict]:
        if self._redis:
            cached = self._redis.get(f"{self._redis_prefix}conv:{session_key}")
            if cached:
                data = json.loads(cached)
                return data.get("messages", [])[-limit:] if limit else data.get("messages", [])
        
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT messages FROM conversations WHERE session_key = ? ORDER BY created_at DESC LIMIT 1", (session_key,))
            else:
                cursor.execute("SELECT messages FROM conversations WHERE session_key = %s ORDER BY created_at DESC LIMIT 1", (session_key,))
            row = cursor.fetchone()
        
        if row:
            messages = row["messages"] if self._storage == "sqlite" else (row["messages"] or [])
            if self._redis:
                cache_data = json.dumps({"messages": messages})
                self._redis.setex(f"{self._redis_prefix}conv:{session_key}", 3600, cache_data)
            return messages[-limit:] if limit else messages
        return []
    
    # ========== USER PROFILES ==========
    def get_user_profile(self, user_id: str) -> UserProfile:
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("SELECT * FROM user_profiles WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
        
        if row:
            # Helper to parse JSON fields that might be dict/list already (PostgreSQL) or string (SQLite)
            def parse_json(val, default):
                if val is None:
                    return default
                if isinstance(val, str):
                    return json.loads(val) if val else default
                return val
            
            # Helper to convert datetime to string
            def to_isoformat(val):
                if val is None:
                    return None
                if hasattr(val, 'isoformat'):
                    return val.isoformat()
                return val
            
            return UserProfile(
                user_id=row["user_id"], name=row["name"], nickname=row["nickname"],
                preferred_name=row["preferred_name"],
                communication_preferences=parse_json(row["communication_preferences"], {}),
                interests=parse_json(row["interests"], []),
                expertise_areas=parse_json(row["expertise_areas"], []),
                learning_topics=parse_json(row["learning_topics"], []),
                timezone=row["timezone"],
                active_hours=parse_json(row["active_hours"], {}),
                conversation_patterns=parse_json(row["conversation_patterns"], {}),
                emotional_patterns=parse_json(row["emotional_patterns"], {}),
                important_dates=parse_json(row["important_dates"], {}),
                life_context=parse_json(row["life_context"], {}),
                total_interactions=row["total_interactions"] or 0,
                first_interaction=to_isoformat(row["first_interaction"]),
                last_interaction=to_isoformat(row["last_interaction"]),
                updated_at=to_isoformat(row["updated_at"])
            )
        return UserProfile(user_id=user_id)
    
    def learn_user_preference(self, user_id: str, preference_type: str, value: str):
        profile = self.get_user_profile(user_id)
        now = datetime.now().isoformat()
        
        if profile.first_interaction is None:
            profile.first_interaction = now
        profile.last_interaction = now
        profile.total_interactions += 1
        profile.updated_at = now
        
        if preference_type == "interest" and value not in profile.interests:
            profile.interests.append(value)
        elif preference_type == "expertise" and value not in profile.expertise_areas:
            profile.expertise_areas.append(value)
        elif preference_type == "learning" and value not in profile.learning_topics:
            profile.learning_topics.append(value)
        
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("""INSERT OR REPLACE INTO user_profiles 
                    (user_id, name, nickname, preferred_name, communication_preferences, interests,
                     expertise_areas, learning_topics, timezone, active_hours, conversation_patterns,
                     emotional_patterns, important_dates, life_context, total_interactions,
                     first_interaction, last_interaction, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (profile.user_id, profile.name, profile.nickname, profile.preferred_name,
                     json.dumps(profile.communication_preferences), json.dumps(profile.interests),
                     json.dumps(profile.expertise_areas), json.dumps(profile.learning_topics),
                     profile.timezone, json.dumps(profile.active_hours),
                     json.dumps(profile.conversation_patterns), json.dumps(profile.emotional_patterns),
                     json.dumps(profile.important_dates), json.dumps(profile.life_context),
                     profile.total_interactions, profile.first_interaction, profile.last_interaction,
                     profile.updated_at))
            else:
                cursor.execute("""INSERT INTO user_profiles 
                    (user_id, name, nickname, preferred_name, communication_preferences, interests,
                     expertise_areas, learning_topics, timezone, active_hours, conversation_patterns,
                     emotional_patterns, important_dates, life_context, total_interactions,
                     first_interaction, last_interaction, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        interests = EXCLUDED.interests,
                        total_interactions = user_profiles.total_interactions + 1,
                        last_interaction = EXCLUDED.last_interaction""",
                    (profile.user_id, profile.name, profile.nickname, profile.preferred_name,
                     psycopg2.extras.Json(profile.communication_preferences), profile.interests,
                     profile.expertise_areas, profile.learning_topics,
                     profile.timezone, psycopg2.extras.Json(profile.active_hours),
                     psycopg2.extras.Json(profile.conversation_patterns), psycopg2.extras.Json(profile.emotional_patterns),
                     psycopg2.extras.Json(profile.important_dates), psycopg2.extras.Json(profile.life_context),
                     profile.total_interactions, profile.first_interaction, profile.last_interaction,
                     profile.updated_at))
    
    def export_user_data(self, user_id: str, agent_id: str = None) -> Dict[str, Any]:
        """
        Export all data associated with a user (GDPR-friendly).

        Args:
            user_id: User identifier
            agent_id: Optional agent filter for memories

        Returns:
            JSON-serializable dict with all user data
        """
        # User profile
        profile = self.get_user_profile(user_id)
        profile_data = asdict(profile)

        # Bonds
        bonds_data = None
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT * FROM bonds WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("SELECT * FROM bonds WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if row:
                bonds_data = {k: row[k] for k in row.keys()}
                # Convert datetime objects
                for k, v in bonds_data.items():
                    if hasattr(v, 'isoformat'):
                        bonds_data[k] = v.isoformat()

        # Memories (scoped by agent_id if provided)
        if agent_id:
            memories = self.recall(agent_id=agent_id, limit=10000)
        else:
            memories = self.recall(limit=10000)
        memories_data = [asdict(m) for m in memories]

        # Conversations
        conversations_data = []
        with self._get_cursor() as cursor:
            if agent_id:
                ph = "?" if self._storage == "sqlite" else "%s"
                cursor.execute(f"SELECT * FROM conversations WHERE agent_id = {ph}", (agent_id,))
            else:
                cursor.execute("SELECT * FROM conversations")
            for row in cursor.fetchall():
                conv = {k: row[k] for k in row.keys()}
                for k, v in conv.items():
                    if hasattr(v, 'isoformat'):
                        conv[k] = v.isoformat()
                    elif isinstance(v, str) and k in ("messages", "keywords"):
                        try:
                            conv[k] = json.loads(v)
                        except (json.JSONDecodeError, TypeError):
                            pass
                conversations_data.append(conv)

        # Learning insights
        insights_data = []
        with self._get_cursor() as cursor:
            cursor.execute("SELECT * FROM learning_insights")
            for row in cursor.fetchall():
                insight = {k: row[k] for k in row.keys()}
                for k, v in insight.items():
                    if hasattr(v, 'isoformat'):
                        insight[k] = v.isoformat()
                insights_data.append(insight)

        return {
            "user_id": user_id,
            "exported_at": datetime.now().isoformat(),
            "profile": profile_data,
            "bonds": bonds_data,
            "memories": memories_data,
            "memories_count": len(memories_data),
            "conversations": conversations_data,
            "conversations_count": len(conversations_data),
            "learning_insights": insights_data,
        }

    # ========== BOND / RELATIONSHIP EVOLUTION ==========

    # Bond levels: maps level thresholds to names and descriptions
    BOND_LEVELS = [
        (0.0, "stranger", "No established relationship"),
        (0.1, "acquaintance", "Initial interactions, surface-level"),
        (0.3, "familiar", "Regular interactions, some trust built"),
        (0.5, "companion", "Strong rapport, mutual understanding"),
        (0.7, "trusted", "Deep trust, shared history, reliable"),
        (0.9, "bonded", "Profound connection, high loyalty"),
    ]

    # Milestones: interaction count thresholds that unlock relationship events
    BOND_MILESTONES = {
        1: "First conversation",
        10: "Getting acquainted",
        50: "Building rapport",
        100: "Established relationship",
        250: "Long-term companion",
        500: "Veteran bond",
        1000: "Legendary bond",
    }

    def get_bond(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the bond/relationship data for a user.

        Args:
            user_id: User identifier

        Returns:
            Dict with bond level, score, interaction history, milestones, and
            relationship stage name. Returns None if no bond exists.
        """
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT * FROM bonds WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("SELECT * FROM bonds WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()

        if not row:
            return None

        bond = {k: row[k] for k in row.keys()}
        # Convert datetime objects
        for k, v in bond.items():
            if hasattr(v, 'isoformat'):
                bond[k] = v.isoformat()
            elif isinstance(v, str) and k == "milestones":
                try:
                    bond[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    bond[k] = []

        # Add computed relationship stage
        level = bond.get("level", 0.0) or 0.0
        stage_name = "stranger"
        stage_desc = "No established relationship"
        for threshold, name, desc in self.BOND_LEVELS:
            if level >= threshold:
                stage_name = name
                stage_desc = desc
        bond["stage"] = stage_name
        bond["stage_description"] = stage_desc

        return bond

    def update_bond(self, user_id: str, agent_id: str = None,
                    messages: List[Dict[str, str]] = None,
                    sentiment_override: float = None) -> Dict[str, Any]:
        """
        Update the bond/relationship with a user based on interaction.

        Bond grows through:
        - Interaction frequency (each interaction increases score)
        - Positive sentiment (detected from messages)
        - Consistency (regular interactions build trust)
        - Milestone achievements (unlock at interaction thresholds)

        Bond can decrease through:
        - Negative sentiment (frustration, anger)
        - Long inactivity (slow decay, gentler than trait decay)

        Args:
            user_id: User identifier
            agent_id: Optional agent identifier (for logging)
            messages: Optional messages from current interaction
            sentiment_override: Optional explicit sentiment (-1.0 to 1.0)

        Returns:
            Updated bond dict
        """
        now = datetime.now()
        now_str = now.isoformat()

        # Get existing bond or create new one
        existing = self.get_bond(user_id)

        if existing:
            level = float(existing.get("level", 0.0) or 0.0)
            score = int(existing.get("score", 0) or 0)
            total = int(existing.get("total_interactions", 0) or 0)
            first_interaction = existing.get("first_interaction", now_str)
            milestones_raw = existing.get("milestones", [])
            if isinstance(milestones_raw, str):
                try:
                    milestones = json.loads(milestones_raw)
                except (json.JSONDecodeError, TypeError):
                    milestones = []
            else:
                milestones = milestones_raw or []
        else:
            level = 0.0
            score = 0
            total = 0
            first_interaction = now_str
            milestones = []

        # Calculate sentiment from messages
        sentiment = 0.0
        if sentiment_override is not None:
            sentiment = sentiment_override
        elif messages:
            sentiment = self._calculate_bond_sentiment(messages)

        # Update interaction count and score
        total += 1
        # Score increases more with positive sentiment, decreases with negative
        score_delta = max(1, int(3 + sentiment * 5))  # 1-8 points per interaction
        if sentiment < -0.3:
            score_delta = int(sentiment * 3)  # Negative: lose 0-3 points
        score = max(0, score + score_delta)

        # Calculate bond level (0.0 to 1.0)
        # Uses logarithmic growth: rapid early, slow later
        # Sentiment-adjusted: negative interactions slow growth
        base_level = min(1.0, math.log(1 + total / 10) / math.log(1 + 100))
        sentiment_factor = 1.0 + (sentiment * 0.1)  # ±10% based on sentiment
        level = max(0.0, min(1.0, base_level * sentiment_factor))

        # Apply inactivity decay if applicable
        if existing and existing.get("last_interaction"):
            try:
                last = datetime.fromisoformat(existing["last_interaction"])
                days_since = (now - last).total_seconds() / 86400
                if days_since > 7:
                    # Gentle decay: 60-day half-life for bond level
                    decay = math.exp(-0.693 * days_since / 60)
                    level = 0.5 * level + 0.5 * level * decay  # Partial decay
            except (ValueError, TypeError):
                pass

        # Check for new milestones
        new_milestones = []
        achieved = set(m.get("name", "") if isinstance(m, dict) else str(m) for m in milestones)
        for threshold, name in self.BOND_MILESTONES.items():
            if total >= threshold and name not in achieved:
                milestone = {"name": name, "threshold": threshold, "achieved_at": now_str}
                milestones.append(milestone)
                new_milestones.append(milestone)

        # Persist
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    """INSERT INTO bonds (user_id, level, score, total_interactions,
                       first_interaction, last_interaction, milestones, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET
                       level = excluded.level, score = excluded.score,
                       total_interactions = excluded.total_interactions,
                       last_interaction = excluded.last_interaction,
                       milestones = excluded.milestones, updated_at = excluded.updated_at""",
                    (user_id, level, score, total, first_interaction, now_str,
                     json.dumps(milestones), now_str, now_str))
            else:
                cursor.execute(
                    """INSERT INTO bonds (user_id, level, score, total_interactions,
                       first_interaction, last_interaction, milestones, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(user_id) DO UPDATE SET
                       level = EXCLUDED.level, score = EXCLUDED.score,
                       total_interactions = EXCLUDED.total_interactions,
                       last_interaction = EXCLUDED.last_interaction,
                       milestones = EXCLUDED.milestones, updated_at = EXCLUDED.updated_at""",
                    (user_id, level, score, total, first_interaction, now_str,
                     json.dumps(milestones), now_str, now_str))

        result = {
            "user_id": user_id,
            "level": level,
            "score": score,
            "total_interactions": total,
            "first_interaction": first_interaction,
            "last_interaction": now_str,
            "milestones": milestones,
            "new_milestones": new_milestones,
        }

        # Add stage info
        stage_name = "stranger"
        for threshold, name, desc in self.BOND_LEVELS:
            if level >= threshold:
                stage_name = name
        result["stage"] = stage_name

        if new_milestones:
            logger.info(f"Bond milestone achieved for {user_id}: {[m['name'] for m in new_milestones]}")

        return result

    def _calculate_bond_sentiment(self, messages: List[Dict[str, str]]) -> float:
        """
        Calculate average sentiment from conversation messages.
        Returns value between -1.0 (very negative) and 1.0 (very positive).
        """
        positive_words = {
            "thanks", "thank you", "great", "awesome", "perfect", "love", "amazing",
            "excellent", "brilliant", "helpful", "appreciate", "wonderful", "fantastic",
            "good", "nice", "cool", "sweet", "happy", "glad", "pleased",
        }
        negative_words = {
            "wrong", "bad", "terrible", "awful", "hate", "angry", "frustrated",
            "annoyed", "disappointed", "useless", "horrible", "stupid", "broken",
            "worst", "fail", "garbage", "trash", "pathetic", "incompetent",
        }

        total_score = 0.0
        count = 0

        for msg in messages:
            if msg.get("role") not in ("user", "human"):
                continue
            content = msg.get("content", "").lower()
            if not content:
                continue

            words = set(content.split())
            pos = len(words & positive_words)
            neg = len(words & negative_words)

            if pos + neg > 0:
                total_score += (pos - neg) / (pos + neg)
                count += 1

        if count == 0:
            return 0.1  # Slight positive bias — they're engaging at all

        return max(-1.0, min(1.0, total_score / count))

    # ========== MOOD/INTENT DETECTION ==========
    def detect_user_mood(self, message: str) -> Dict[str, Any]:
        message_lower = message.lower()
        mood_keywords = {
            "happy": ["great", "awesome", "love", "excellent", "happy", "wonderful", "amazing", "excited"],
            "frustrated": ["annoying", "hate", "stupid", "frustrated", "angry", "terrible", "worst"],
            "stressed": ["busy", "overwhelmed", "stressed", "anxious", "worry", "panic"],
            "curious": ["how", "why", "what", "tell me", "explain", "wondering", "interested"],
            "sad": ["unfortunately", "sad", "disappointed", "sorry", "unhappy"],
        }
        mood_scores = {}
        for mood, keywords in mood_keywords.items():
            matches = sum(1 for kw in keywords if kw in message_lower)
            if matches > 0:
                mood_scores[mood] = matches / len(keywords)
        
        if mood_scores:
            top_mood = max(mood_scores, key=mood_scores.get)
            return {"mood": top_mood, "confidence": min(0.9, 0.3 + mood_scores[top_mood] * 0.3), "all_moods": mood_scores}
        return {"mood": "neutral", "confidence": 0.5, "all_moods": {}}
    
    def detect_user_intent(self, message: str) -> str:
        message_lower = message.lower().strip()
        if any(greet in message_lower for greet in ["hello", "hi", "hey", "good morning"]):
            return "greeting"
        elif "?" in message or message_lower.startswith(("what", "how", "why", "can you", "could you")):
            return "question"
        elif any(req in message_lower for req in ["please", "can you", "i want", "i need"]):
            return "request"
        elif any(fb in message_lower for fb in ["actually", "no that's", "wrong"]):
            return "feedback"
        elif any(bye in message_lower for bye in ["bye", "goodbye", "later"]):
            return "farewell"
        return "statement"
    
    # ========== SOUL / TRAIT EVOLUTION ==========

    # Bidirectional trait signal patterns: positive signals increase, negative decrease
    TRAIT_SIGNALS = {
        "humor": {
            "positive": ["lol", "haha", "funny", "joke", "laugh", "hilarious", "rofl", "lmao",
                         "😂", "🤣", "that's great", "made me laugh", "love the humor"],
            "negative": ["not funny", "stop joking", "be serious", "this isn't a joke",
                         "too casual", "unprofessional", "no jokes please", "focus please"],
        },
        "empathy": {
            "positive": ["feel", "struggle", "hard time", "difficult", "sorry", "understand",
                         "support", "care", "worried", "scared", "lonely", "hurt", "thank you for understanding"],
            "negative": ["don't patronize", "stop being condescending", "just answer",
                         "i don't need sympathy", "skip the feelings", "not looking for empathy"],
        },
        "curiosity": {
            "positive": ["why", "how does", "interesting", "wonder", "curious", "explore",
                         "learn", "discover", "fascinating", "tell me more", "elaborate",
                         "dig deeper", "what's behind"],
            "negative": ["too many questions", "just do it", "stop asking", "i already know",
                         "no more questions", "don't need explanation", "skip the why"],
        },
        "creativity": {
            "positive": ["idea", "imagine", "what if", "create", "design", "build", "invent",
                         "brainstorm", "concept", "prototype", "innovative", "alternative"],
            "negative": ["keep it simple", "don't overcomplicate", "standard approach",
                         "no need to reinvent", "just the basics", "stick to convention", "boring is fine"],
        },
        "helpfulness": {
            "positive": ["thanks", "thank you", "helpful", "solved", "perfect", "exactly",
                         "great job", "appreciate", "awesome", "worked", "brilliant", "nailed it"],
            "negative": ["wrong", "not what i asked", "that's not helpful", "you misunderstood",
                         "try again", "that's incorrect", "completely off", "useless"],
        },
        "honesty": {
            "positive": ["actually", "you're right", "fair point", "i was wrong", "good point",
                         "correction", "honest", "truthful", "accurate", "well put"],
            "negative": ["that's not true", "you're lying", "misleading", "inaccurate",
                         "don't sugarcoat", "be real with me", "stop hedging"],
        },
        "conciseness": {
            "positive": ["tl;dr", "too long", "be brief", "shorter please", "just the answer",
                         "in a nutshell", "summarize", "keep it short", "get to the point",
                         "too verbose", "too wordy", "just the code", "just give me",
                         "don't explain", "skip the explanation", "no long",
                         "less words", "cut it down", "short and sweet", "concise",
                         "succinct", "brief", "pithy"],
            "negative": ["more detail", "elaborate", "explain more", "tell me more",
                         "expand on that", "go deeper", "need more info", "too brief",
                         "that's not enough detail", "walk me through", "step by step",
                         "can you explain"],
        },
        "formality": {
            "positive": ["professional", "formal", "proper", "sir", "ma'am", "respectfully",
                         "per our discussion", "as discussed", "please note"],
            "negative": ["chill", "relax", "casual", "dude", "bro", "lol", "no need to be formal",
                         "lighten up", "too stiff", "we're friends"],
        },
        "directness": {
            "positive": ["just tell me", "straight answer", "be direct", "yes or no",
                         "bottom line", "cut to the chase", "don't beat around the bush",
                         "stop hedging", "blunt"],
            "negative": ["be diplomatic", "be gentle", "soften it", "ease into it",
                         "be tactful", "sugar coat", "let me down easy"],
        },
    }

    def get_soul(self, agent_id: str, user_id: str = None) -> Dict[str, Any]:
        """
        Get the soul/personality traits for an agent, optionally merged with
        per-user trait overrides.

        Args:
            agent_id: Agent identifier
            user_id: Optional user ID — if provided, returns traits merged with
                     per-user overrides (user-specific traits take precedence)

        Returns:
            Dict with traits, interaction_count, and metadata
        """
        # Get base agent soul
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute("SELECT * FROM souls WHERE agent_id = ?", (agent_id,))
            else:
                cursor.execute("SELECT * FROM souls WHERE agent_id = %s", (agent_id,))
            row = cursor.fetchone()

        if row:
            traits = row["traits"]
            if isinstance(traits, str):
                traits = json.loads(traits) if traits else dict(DEFAULT_TRAITS)
            # Ensure new traits exist (migration from old 6-trait schema)
            for k, v in DEFAULT_TRAITS.items():
                if k not in traits:
                    traits[k] = v

            def _safe_get(r, key, default=None):
                try:
                    return r[key]
                except (KeyError, IndexError):
                    return default

            last_decay_at = _safe_get(row, "last_decay_at")
            soul = {
                "agent_id": agent_id,
                "traits": traits,
                "preferred_topics": json.loads(row["preferred_topics"]) if isinstance(row["preferred_topics"], str) and row["preferred_topics"] else (row["preferred_topics"] or []),
                "interaction_count": row["interaction_count"] or 0,
                "last_decay_at": last_decay_at,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        else:
            # Create new soul with default traits
            now = datetime.now().isoformat()
            with self._get_cursor() as cursor:
                if self._storage == "sqlite":
                    cursor.execute(
                        """INSERT OR IGNORE INTO souls (agent_id, traits, preferred_topics, interaction_count, last_decay_at, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (agent_id, json.dumps(DEFAULT_TRAITS), json.dumps([]), 0, now, now, now)
                    )
                else:
                    cursor.execute(
                        """INSERT INTO souls (agent_id, traits, preferred_topics, interaction_count, last_decay_at, created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (agent_id) DO NOTHING""",
                        (agent_id, json.dumps(DEFAULT_TRAITS), json.dumps([]), 0, now, now, now)
                    )
            soul = {
                "agent_id": agent_id,
                "traits": dict(DEFAULT_TRAITS),
                "preferred_topics": [],
                "interaction_count": 0,
                "last_decay_at": now,
                "created_at": now,
                "updated_at": now,
            }

        # Apply trait decay before returning
        soul["traits"] = self._apply_trait_decay(soul)

        # Merge per-user traits if user_id provided
        if user_id:
            user_traits = self._get_user_traits(agent_id, user_id)
            if user_traits:
                # User-specific traits override base traits with a weighted blend:
                # 70% user-specific, 30% base agent trait
                merged = dict(soul["traits"])
                for trait_name, user_val in user_traits["traits"].items():
                    if trait_name in merged:
                        merged[trait_name] = 0.7 * user_val + 0.3 * merged[trait_name]
                soul["traits"] = merged
                soul["user_interaction_count"] = user_traits["interaction_count"]

        return soul

    def _get_user_traits(self, agent_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get per-user trait overrides for an agent+user pair."""
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    "SELECT * FROM soul_user_traits WHERE agent_id = ? AND user_id = ?",
                    (agent_id, user_id))
            else:
                cursor.execute(
                    "SELECT * FROM soul_user_traits WHERE agent_id = %s AND user_id = %s",
                    (agent_id, user_id))
            row = cursor.fetchone()

        if not row:
            return None

        traits = row["traits"]
        if isinstance(traits, str):
            traits = json.loads(traits)
        return {
            "traits": traits,
            "interaction_count": row["interaction_count"] or 0,
        }

    def _apply_trait_decay(self, soul: Dict[str, Any]) -> Dict[str, float]:
        """
        Apply time-based trait decay. Traits drift toward 0.5 (neutral) when
        not actively reinforced. Uses exponential decay with configurable half-life.

        This prevents traits from permanently ratcheting in one direction.
        """
        traits = soul["traits"]
        last_decay_str = soul.get("last_decay_at")
        if not last_decay_str:
            return traits

        try:
            last_decay = datetime.fromisoformat(last_decay_str)
        except (ValueError, TypeError):
            return traits

        now = datetime.now()
        days_elapsed = (now - last_decay).total_seconds() / 86400

        if days_elapsed < 1:
            # Don't decay more than once per day
            return traits

        # Decay factor: how much to pull toward 0.5
        # After TRAIT_DECAY_HALF_LIFE_DAYS days, the distance from 0.5 halves
        decay_factor = math.exp(-0.693 * days_elapsed / TRAIT_DECAY_HALF_LIFE_DAYS)

        decayed = {}
        for name, value in traits.items():
            distance = value - 0.5
            decayed[name] = 0.5 + distance * decay_factor

        # Persist decayed traits + update last_decay_at
        now_str = now.isoformat()
        try:
            with self._get_cursor() as cursor:
                if self._storage == "sqlite":
                    cursor.execute(
                        "UPDATE souls SET traits = ?, last_decay_at = ?, updated_at = ? WHERE agent_id = ?",
                        (json.dumps(decayed), now_str, now_str, soul["agent_id"]))
                else:
                    cursor.execute(
                        "UPDATE souls SET traits = %s, last_decay_at = %s, updated_at = %s WHERE agent_id = %s",
                        (json.dumps(decayed), now_str, now_str, soul["agent_id"]))
        except Exception as e:
            logger.warning(f"Failed to persist trait decay: {e}")

        return decayed

    def evolve_traits(self, agent_id: str, interaction_signals: Dict[str, float],
                      user_id: str = None) -> Dict[str, float]:
        """
        Evolve personality traits based on interaction signals.
        Supports bidirectional signals: positive values increase traits,
        negative values decrease them.
        Learning rate decreases as interaction_count grows, preventing wild swings.

        Args:
            agent_id: Agent identifier
            interaction_signals: Dict mapping trait names to signal strengths
                                 (e.g., {"humor": 0.3, "conciseness": -0.5})
                                 Positive = reinforce, Negative = suppress
            user_id: Optional — if provided, also updates per-user traits

        Returns:
            Updated traits dict
        """
        soul = self.get_soul(agent_id)
        traits = soul["traits"]
        count = soul["interaction_count"]

        # Learning rate decreases over time: starts at 0.1, approaches 0.01
        learning_rate = max(0.01, 0.1 / (1 + count / 100))

        for trait_name, signal in interaction_signals.items():
            if trait_name in traits:
                new_value = traits[trait_name] + signal * learning_rate
                traits[trait_name] = max(0.0, min(1.0, new_value))

        now = datetime.now().isoformat()
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    """UPDATE souls SET traits = ?, interaction_count = ?, last_decay_at = ?, updated_at = ?
                       WHERE agent_id = ?""",
                    (json.dumps(traits), count + 1, now, now, agent_id))
            else:
                cursor.execute(
                    """UPDATE souls SET traits = %s, interaction_count = %s, last_decay_at = %s, updated_at = %s
                       WHERE agent_id = %s""",
                    (json.dumps(traits), count + 1, now, now, agent_id))

        # Update per-user traits if user_id provided
        if user_id:
            self._evolve_user_traits(agent_id, user_id, interaction_signals)

        logger.info(f"Evolved traits for {agent_id}: {traits}")
        return traits

    def _evolve_user_traits(self, agent_id: str, user_id: str,
                            signals: Dict[str, float]):
        """Evolve per-user trait overrides for an agent+user pair."""
        user_traits = self._get_user_traits(agent_id, user_id)
        now = datetime.now().isoformat()

        if user_traits:
            traits = user_traits["traits"]
            count = user_traits["interaction_count"]
        else:
            traits = dict(DEFAULT_TRAITS)
            count = 0

        learning_rate = max(0.01, 0.15 / (1 + count / 50))  # Slightly faster learning per-user

        for trait_name, signal in signals.items():
            if trait_name in traits:
                new_value = traits[trait_name] + signal * learning_rate
                traits[trait_name] = max(0.0, min(1.0, new_value))

        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    """INSERT INTO soul_user_traits (agent_id, user_id, traits, interaction_count, last_decay_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(agent_id, user_id) DO UPDATE SET
                       traits = excluded.traits, interaction_count = excluded.interaction_count,
                       last_decay_at = excluded.last_decay_at, updated_at = excluded.updated_at""",
                    (agent_id, user_id, json.dumps(traits), count + 1, now, now, now))
            else:
                cursor.execute(
                    """INSERT INTO soul_user_traits (agent_id, user_id, traits, interaction_count, last_decay_at, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(agent_id, user_id) DO UPDATE SET
                       traits = EXCLUDED.traits, interaction_count = EXCLUDED.interaction_count,
                       last_decay_at = EXCLUDED.last_decay_at, updated_at = EXCLUDED.updated_at""",
                    (agent_id, user_id, json.dumps(traits), count + 1, now, now, now))

    def analyze_interaction_for_traits(self, message: str, response: str = None) -> Dict[str, float]:
        """
        Derive trait evolution signals from a user message and optional agent response.
        Uses bidirectional keyword matching — detects both positive and negative signals.

        Args:
            message: User's message
            response: Optional agent response

        Returns:
            Dict of trait signals. Positive values reinforce the trait,
            negative values suppress it.
        """
        text = (message + " " + (response or "")).lower()
        signals = {}

        for trait_name, patterns in self.TRAIT_SIGNALS.items():
            pos_score = sum(1 for w in patterns["positive"] if w in text)
            neg_score = sum(1 for w in patterns["negative"] if w in text)

            # Net signal: positive patterns push up, negative push down
            if pos_score > 0 or neg_score > 0:
                net = (pos_score * 0.3) - (neg_score * 0.4)  # Negative signals weighted slightly higher
                signals[trait_name] = max(-1.0, min(1.0, net))

        return signals

    def analyze_interaction_for_traits_llm(self, message: str, response: str = None,
                                           llm_fn: Callable = None) -> Dict[str, float]:
        """
        LLM-powered trait signal detection. Falls back to rule-based if no llm_fn.

        Args:
            message: User's message
            response: Optional agent response
            llm_fn: Callable(prompt: str) -> str that returns JSON

        Returns:
            Dict of trait signals
        """
        if not llm_fn:
            return self.analyze_interaction_for_traits(message, response)

        trait_names = list(DEFAULT_TRAITS.keys())
        prompt = f"""Analyze this interaction and determine personality trait signals.

User message: {message}
{f'Agent response: {response}' if response else ''}

For each trait, return a signal between -1.0 (suppress) and 1.0 (reinforce).
Only include traits with non-zero signals.

Available traits: {', '.join(trait_names)}

Trait descriptions:
- humor: Does the user want/enjoy humor in responses?
- empathy: Does the user want emotional understanding?
- curiosity: Does the user want deep exploration and questions?
- creativity: Does the user want creative/novel approaches?
- helpfulness: Is the user satisfied with the help provided?
- honesty: Does the user want direct truthfulness?
- conciseness: Does the user want shorter, more concise responses?
- formality: Does the user want formal/professional communication?
- directness: Does the user want straight answers without hedging?

Return ONLY valid JSON like: {{"humor": 0.3, "conciseness": -0.5}}
"""
        try:
            result = llm_fn(prompt)
            # Parse JSON from response
            json_match = re.search(r'\{[^}]+\}', result)
            if json_match:
                signals = json.loads(json_match.group())
                # Validate and clamp
                valid = {}
                for k, v in signals.items():
                    if k in DEFAULT_TRAITS and isinstance(v, (int, float)):
                        valid[k] = max(-1.0, min(1.0, float(v)))
                return valid
        except Exception as e:
            logger.warning(f"LLM trait analysis failed, falling back to rule-based: {e}")

        return self.analyze_interaction_for_traits(message, response)

    def get_trait_influenced_guidance(self, agent_id: str, user_id: str = None) -> Dict[str, Any]:
        """
        Translate current traits into response guidance adjustments.
        Uses per-user traits when available for personalized guidance.

        Args:
            agent_id: Agent identifier
            user_id: Optional user ID for personalized traits

        Returns:
            Dict of guidance overrides based on trait levels
        """
        soul = self.get_soul(agent_id, user_id=user_id)
        traits = soul["traits"]
        guidance = {}

        # Humor: threshold-based with intensity
        humor_level = traits.get("humor", 0.5)
        guidance["use_humor"] = humor_level > 0.6
        if humor_level > 0.8:
            guidance["humor_style"] = "playful"
        elif humor_level < 0.3:
            guidance["humor_style"] = "none"

        # Empathy
        empathy_level = traits.get("empathy", 0.5)
        if empathy_level > 0.6:
            guidance["show_empathy"] = True
            guidance["be_encouraging"] = True
        elif empathy_level < 0.3:
            guidance["show_empathy"] = False

        # Curiosity
        guidance["ask_followups"] = traits.get("curiosity", 0.5) > 0.6

        # Creativity
        guidance["suggest_alternatives"] = traits.get("creativity", 0.5) > 0.6

        # Helpfulness
        guidance["proactive_suggestions"] = traits.get("helpfulness", 0.5) > 0.7

        # Honesty -> directness in tone
        if traits.get("honesty", 0.5) > 0.6:
            guidance["tone"] = "direct"

        # Conciseness — new: graduated levels
        conciseness = traits.get("conciseness", 0.5)
        if conciseness > 0.7:
            guidance["verbosity"] = "terse"
            guidance["max_paragraphs"] = 2
        elif conciseness > 0.5:
            guidance["verbosity"] = "concise"
        elif conciseness < 0.3:
            guidance["verbosity"] = "detailed"

        # Formality — new
        formality = traits.get("formality", 0.5)
        if formality > 0.7:
            guidance["formality"] = "formal"
            guidance["use_emoji"] = False
        elif formality > 0.5:
            guidance["formality"] = "professional"
        elif formality < 0.3:
            guidance["formality"] = "casual"
            guidance["use_emoji"] = True

        # Directness — new
        directness = traits.get("directness", 0.5)
        if directness > 0.7:
            guidance["hedging"] = "none"
            guidance["lead_with_answer"] = True
        elif directness < 0.3:
            guidance["hedging"] = "diplomatic"
            guidance["lead_with_answer"] = False

        # Match energy based on overall trait levels
        avg_energy = sum(traits.values()) / len(traits) if traits else 0.5
        guidance["match_energy"] = avg_energy > 0.6

        return guidance

    def get_trait_biased_weights(self, agent_id: str, user_id: str = None) -> Dict[str, float]:
        """
        Return retrieval weight adjustments based on current traits.
        Traits influence which memories get prioritized during recall.

        For example, a high-empathy agent weights emotional/personal memories higher.
        A high-conciseness agent weights summaries over episodes.

        Args:
            agent_id: Agent identifier
            user_id: Optional user ID for personalized traits

        Returns:
            Dict of memory_kind weight multipliers
        """
        soul = self.get_soul(agent_id, user_id=user_id)
        traits = soul["traits"]

        # Base weight multipliers for memory kinds based on trait levels
        weights = {}

        # High empathy -> boost preference and episode memories
        empathy = traits.get("empathy", 0.5)
        weights["preference"] = 1.0 + (empathy - 0.5) * 0.6
        weights["episode"] = 1.0 + (empathy - 0.5) * 0.4

        # High helpfulness -> boost task and procedure memories
        helpfulness = traits.get("helpfulness", 0.5)
        weights["task"] = 1.0 + (helpfulness - 0.5) * 0.6
        weights["procedure"] = 1.0 + (helpfulness - 0.5) * 0.4

        # High conciseness -> boost summary, reduce episodes
        conciseness = traits.get("conciseness", 0.5)
        weights["summary"] = 1.0 + (conciseness - 0.5) * 0.8
        if "episode" in weights:
            weights["episode"] *= 1.0 - (conciseness - 0.5) * 0.4

        # High curiosity -> boost facts and constraints
        curiosity = traits.get("curiosity", 0.5)
        weights["fact"] = 1.0 + (curiosity - 0.5) * 0.4
        weights["constraint"] = 1.0 + (curiosity - 0.5) * 0.3

        return weights

    # ========== AUTO MEMORY / PROCESS TURN ==========

    def process_turn(self, agent_id: str, user_id: str, user_message: str,
                     assistant_response: str = None, session_key: str = None,
                     llm_fn: Callable = None, auto_extract: bool = True,
                     scope: str = "private", scope_id: str = "") -> Dict[str, Any]:
        """
        Single entry point for automatic memory management per conversation turn.

        Call this after each user message (and optionally after the assistant response)
        to automatically:
        1. Extract and store important memories from the exchange
        2. Evolve personality traits based on interaction signals
        3. Update the user bond/relationship
        4. Update user profile patterns
        5. Return the full context for the next response

        This is the recommended way to integrate ClawBrain into an agent loop.

        Usage:
            brain = Brain()
            # After each turn:
            ctx = brain.process_turn(
                agent_id="my_agent",
                user_id="user123",
                user_message="I prefer Python and concise answers",
                assistant_response="Got it!",
                session_key="session_abc",
            )
            # ctx contains everything needed for the next LLM call

        Args:
            agent_id: Agent identifier
            user_id: User identifier
            user_message: The user's message
            assistant_response: Optional assistant response (if available)
            session_key: Optional session key for conversation tracking
            llm_fn: Optional LLM callable for higher-quality extraction
            auto_extract: Whether to extract memories (default True)
            scope: Memory scope for extracted memories
            scope_id: Scope identifier

        Returns:
            Dict with:
            - context: Full context dict (same as get_full_context)
            - extracted: List of Memory objects extracted this turn
            - trait_signals: Dict of detected trait signals
            - bond: Updated bond data
            - new_milestones: List of any new bond milestones achieved
        """
        messages = [{"role": "user", "content": user_message}]
        if assistant_response:
            messages.append({"role": "assistant", "content": assistant_response})

        # 1. Detect trait signals first (for return value)
        trait_signals = {}
        try:
            if llm_fn:
                trait_signals = self.analyze_interaction_for_traits_llm(
                    user_message, assistant_response, llm_fn)
            else:
                trait_signals = self.analyze_interaction_for_traits(
                    user_message, assistant_response)
        except Exception as e:
            logger.warning(f"Trait analysis failed: {e}")

        # 2. Evolve traits (once — ingest_conversation will skip if we do it here)
        if trait_signals:
            try:
                self.evolve_traits(agent_id, trait_signals, user_id=user_id)
            except Exception as e:
                logger.warning(f"Trait evolution failed: {e}")

        # 3. Update bond (once — capture result for return)
        bond = None
        new_milestones = []
        try:
            if user_id and user_id != "default":
                bond = self.update_bond(user_id, agent_id, messages)
                new_milestones = bond.get("new_milestones", [])
        except Exception as e:
            logger.warning(f"Bond update failed: {e}")

        # 4. Extract memories (skip trait/bond in ingest since we already did them)
        extracted = []
        if auto_extract:
            try:
                extracted = self._extract_memories_only(
                    agent_id=agent_id,
                    user_id=user_id,
                    messages=messages,
                    llm_fn=llm_fn,
                    scope=scope,
                    scope_id=scope_id,
                )
            except Exception as e:
                logger.warning(f"Auto-extraction failed: {e}")

        # 5. Store conversation turn
        try:
            if session_key:
                self.remember_conversation(session_key, messages, agent_id=agent_id)
        except Exception as e:
            logger.warning(f"Conversation storage failed: {e}")

        # 6. Build full context for next response (skip trait/bond mutation
        #    since we already handled it above)
        context = self.get_full_context(
            session_key=session_key or f"auto_{agent_id}_{user_id}",
            user_id=user_id,
            agent_id=agent_id,
            message=user_message,
        )

        return {
            "context": context,
            "extracted": extracted,
            "extracted_count": len(extracted),
            "trait_signals": trait_signals,
            "bond": bond,
            "new_milestones": new_milestones,
        }

    # ========== FULL CONTEXT ==========
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count using ~4 chars per token heuristic."""
        if not text:
            return 0
        return len(str(text)) // 4

    def get_full_context(self, session_key: str, user_id: str = "default", agent_id: str = "assistant",
                         message: str = None, max_tokens: int = None) -> Dict[str, Any]:
        """
        Assemble full context for LLM prompt injection.

        Args:
            session_key: Session identifier
            user_id: User identifier
            agent_id: Agent identifier
            message: Current user message for mood/intent analysis
            max_tokens: Optional token budget - limits context size to fit model window

        Returns:
            JSON-serializable dict with user profile, memories, conversation state, etc.
        """
        now = datetime.now()
        message_analysis = {}
        if message:
            message_analysis = {"mood": self.detect_user_mood(message), "intent": self.detect_user_intent(message)}

        conversation_state = self.get_conversation(session_key)
        user_profile = self.get_user_profile(user_id)

        # Request more memories if we have a token budget (we'll trim later)
        # Use hybrid retrieval with the current message as query
        memory_limit = 20 if max_tokens else 10
        memories = self.recall(
            agent_id=agent_id,
            query=message if message else None,
            limit=memory_limit,
        )

        # Build response guidance
        response_guidance = {
            "tone": "friendly", "formality": user_profile.communication_preferences.get("formality", "casual"),
            "verbosity": user_profile.communication_preferences.get("verbosity", "concise"),
            "use_humor": user_profile.communication_preferences.get("use_humor", True),
            "use_emoji": user_profile.communication_preferences.get("use_emoji", True),
            "show_empathy": False, "be_encouraging": True, "match_energy": False, "response_type": "conversational",
        }

        # Apply trait-influenced guidance (per-user if available)
        try:
            trait_guidance = self.get_trait_influenced_guidance(agent_id, user_id=user_id)
            response_guidance.update(trait_guidance)
        except Exception:
            pass

        # Get soul data (per-user merged)
        soul_data = None
        try:
            soul_data = self.get_soul(agent_id, user_id=user_id)
        except Exception:
            pass

        # Get bond data
        bond_data = None
        try:
            if user_id and user_id != "default":
                bond_data = self.get_bond(user_id)
        except Exception:
            pass

        # Evolve traits based on current message (per-user)
        if message:
            try:
                signals = self.analyze_interaction_for_traits(message)
                if signals:
                    self.evolve_traits(agent_id, signals, user_id=user_id)
            except Exception:
                pass

            # Update bond
            try:
                if user_id and user_id != "default":
                    self.update_bond(user_id, agent_id, [{"role": "user", "content": message}])
            except Exception:
                pass

        context = {
            "user": {
                "profile": {"name": user_profile.preferred_name or user_profile.name,
                           "interests": user_profile.interests, "expertise": user_profile.expertise_areas},
                "preferred_name": user_profile.preferred_name or user_profile.name,
                "interests": user_profile.interests,
                "communication_style": user_profile.communication_preferences,
            },
            "conversation": {
                "state": {"user_mood": message_analysis.get("mood", {}).get("mood", "neutral") if message_analysis else "neutral",
                         "intent": message_analysis.get("intent", "statement") if message_analysis else "statement"},
                "history": conversation_state,
                "turn_count": len(conversation_state) if conversation_state else 0,
                "current_topic": "",
            },
            "message_analysis": message_analysis,
            "memories": [asdict(m) for m in memories],
            "soul": soul_data,
            "bond": bond_data,
            "time_context": {"time_of_day": now.strftime("%H:%M"), "timestamp": now.isoformat()},
            "response_guidance": response_guidance,
        }

        # Apply token budget if specified
        if max_tokens:
            context = self._apply_token_budget(context, max_tokens)

        return context

    def _apply_token_budget(self, context: Dict[str, Any], max_tokens: int) -> Dict[str, Any]:
        """
        Trim context to fit within a token budget.
        Priority: user profile > response_guidance > message_analysis > soul > memories > conversation history
        """
        total = self._estimate_tokens(json.dumps(context))
        if total <= max_tokens:
            return context

        # Step 1: Trim memories (lowest importance first - they're already sorted desc)
        memories = context.get("memories", [])
        while memories and self._estimate_tokens(json.dumps(context)) > max_tokens:
            memories.pop()  # Remove lowest importance (last in list)
            context["memories"] = memories

        # Step 2: Trim conversation history
        history = context.get("conversation", {}).get("history", [])
        if isinstance(history, list):
            while history and self._estimate_tokens(json.dumps(context)) > max_tokens:
                history.pop(0)  # Remove oldest
                context["conversation"]["history"] = history

        # Step 3: Truncate individual memory content
        if self._estimate_tokens(json.dumps(context)) > max_tokens:
            for mem in context.get("memories", []):
                if len(mem.get("content", "")) > 200:
                    mem["content"] = mem["content"][:200] + "..."

        return context
    
    def process_message(self, message: str, session_key: str, user_id: str = "default", agent_id: str = "assistant") -> Dict[str, Any]:
        return self.get_full_context(session_key, user_id, agent_id, message)
    
    def generate_personality_prompt(self, agent_id: str = "assistant", user_id: str = "default") -> str:
        profile = self.get_user_profile(user_id)
        prompt = f"You are {agent_id}, a personal AI assistant who is helpful and friendly."
        if profile.preferred_name:
            prompt += f" Your human is named {profile.preferred_name}."
        if profile.interests:
            prompt += f" They're interested in: {', '.join(profile.interests[:3])}."
        return prompt
    
    # ========== HELPER METHODS ==========
    def _extract_keywords(self, messages: List[Dict]) -> List[str]:
        keywords = []
        for msg in messages:
            content = msg.get("content", "").lower()
            words = [w for w in content.split() if len(w) > 3 and w not in ["that", "this", "with", "from", "have"]]
            keywords.extend(words[:5])
        return list(set(keywords))[:10]
    
    def _summarize(self, messages: List[Dict]) -> str:
        if not messages:
            return ""
        content = " ".join(m.get("content", "") for m in messages)
        return content[:100] + "..." if len(content) > 100 else content
    
    @contextmanager
    def _get_cursor(self):
        with self._lock:
            if self._storage == "sqlite":
                cursor = self._sqlite_conn.cursor()
                try:
                    yield cursor
                    self._sqlite_conn.commit()
                finally:
                    cursor.close()
            else:
                cursor = self._pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                try:
                    yield cursor
                finally:
                    cursor.close()
    
    def health_check(self) -> Dict[str, bool]:
        return {"storage": self._storage in ["sqlite", "postgresql"], "sqlite": self._storage == "sqlite",
                "postgres": self._storage == "postgresql", "redis": self._redis is not None,
                "backup_dir": self._backup_dir.exists()}
    
    # ========== SYNC/REFRESH METHODS (OpenClaw Integration) ==========
    def sync_memories(self, agent_id: str = "openclaw", since_hours: int = 24) -> Dict[str, Any]:
        """
        Sync and return recent memories for OpenClaw integration.
        Called on gateway startup to refresh memory context.
        
        Args:
            agent_id: Agent identifier
            since_hours: Only sync memories from the last N hours
            
        Returns:
            Dict with sync results including memories count and last sync time
        """
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
        
        with self._get_cursor() as cursor:
            if self._storage == "sqlite":
                cursor.execute(
                    "SELECT COUNT(*) as count FROM memories WHERE agent_id = ? AND created_at > ?",
                    (agent_id, cutoff)
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) as count FROM memories WHERE agent_id = %s AND created_at > %s",
                    (agent_id, cutoff)
                )
            row = cursor.fetchone()
            recent_count = row["count"] if row else 0
        
        # Get all memories count
        memories = self.recall(agent_id=agent_id, limit=100)
        
        return {
            "memories_count": len(memories),
            "recent_memories": recent_count,
            "since_hours": since_hours,
            "storage_backend": self._storage,
            "last_sync": datetime.now().isoformat(),
        }
    
    def refresh_on_startup(self, agent_id: str = "openclaw", user_id: str = "default") -> Dict[str, Any]:
        """
        Refresh brain state on OpenClaw service startup.
        This is the main method called by the OpenClaw plugin on gateway:startup.
        
        Args:
            agent_id: Agent identifier
            user_id: User identifier
            
        Returns:
            Dict with full context and sync status
        """
        # Health check first
        health = self.health_check()
        if not health.get("storage"):
            return {
                "success": False,
                "error": "Storage backend not available",
                "health": health,
            }
        
        # Sync memories
        sync_result = self.sync_memories(agent_id=agent_id)
        
        # Get user profile
        profile = self.get_user_profile(user_id)
        
        # Get full context for the agent
        context = self.get_full_context(
            session_key=f"{agent_id}_startup",
            user_id=user_id,
            agent_id=agent_id,
            message=""
        )
        
        # Generate personality prompt
        personality_prompt = self.generate_personality_prompt(
            agent_id=agent_id,
            user_id=user_id
        )
        
        return {
            "success": True,
            "sync": sync_result,
            "user_profile": {
                "name": profile.preferred_name or profile.name,
                "interests": profile.interests,
                "expertise": profile.expertise_areas,
                "total_interactions": profile.total_interactions,
            },
            "context": context,
            "personality_prompt": personality_prompt,
            "health": health,
            "refreshed_at": datetime.now().isoformat(),
        }
    
    def save_session_to_memory(
        self, 
        session_key: str, 
        messages: List[Dict[str, str]], 
        agent_id: str = "openclaw",
        tags: List[str] = None
    ) -> Dict[str, Any]:
        """
        Save a session's messages to memory.
        Called by OpenClaw plugin on command:new event.
        
        Args:
            session_key: Unique session identifier
            messages: List of message dicts with 'role' and 'content'
            agent_id: Agent identifier
            tags: Optional tags for the memory
            
        Returns:
            Dict with save result
        """
        if not messages:
            return {"success": False, "error": "No messages to save"}
        
        # Create a summary of the conversation
        content_parts = []
        for msg in messages[-20:]:  # Last 20 messages
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:500]  # Truncate long messages
            content_parts.append(f"{role}: {content}")
        
        content = "\n".join(content_parts)
        summary = self._summarize(messages[-5:])  # Summary of last 5
        
        # Store as conversation memory
        memory = self.remember(
            agent_id=agent_id,
            memory_type="conversation",
            content=content,
            key=f"session_{session_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            tags=tags or ["session", "conversation"],
            auto_tag=True,
            importance=6,  # Slightly above average importance
        )
        
        # Also save to conversations table
        conv_id = self.remember_conversation(
            session_key=session_key,
            messages=messages,
            agent_id=agent_id,
            summary=summary
        )
        
        return {
            "success": True,
            "memory_id": memory.id,
            "conversation_id": conv_id,
            "messages_saved": len(messages),
            "saved_at": datetime.now().isoformat(),
        }
    
    def get_startup_context(self, agent_id: str = "openclaw", user_id: str = "default") -> str:
        """
        Get a formatted context string for OpenClaw MEMORY.md injection.
        
        Args:
            agent_id: Agent identifier
            user_id: User identifier
            
        Returns:
            Markdown-formatted context string for MEMORY.md
        """
        profile = self.get_user_profile(user_id)
        memories = self.recall(agent_id=agent_id, limit=5)
        
        lines = ["# Memory Context", ""]
        
        # User section
        lines.append("## User Profile")
        if profile.preferred_name or profile.name:
            lines.append(f"- **Name**: {profile.preferred_name or profile.name}")
        if profile.interests:
            lines.append(f"- **Interests**: {', '.join(profile.interests[:5])}")
        if profile.expertise_areas:
            lines.append(f"- **Expertise**: {', '.join(profile.expertise_areas[:5])}")
        if profile.total_interactions:
            lines.append(f"- **Interactions**: {profile.total_interactions}")
        lines.append("")
        
        # Recent memories
        if memories:
            lines.append("## Recent Memories")
            for mem in memories:
                summary = mem.summary or mem.content[:100]
                lines.append(f"- [{mem.memory_type}] {summary}")
            lines.append("")
        
        # Timestamp
        lines.append(f"_Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
        
        return "\n".join(lines)
    
    def close(self):
        if hasattr(self, '_sqlite_conn') and self._sqlite_conn:
            self._sqlite_conn.close()
        if self._pg_conn:
            self._pg_conn.close()
        if self._redis:
            self._redis.close()


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model = None
        if EMBEDDINGS_AVAILABLE:
            try:
                self.model = SentenceTransformer(model_name)
            except Exception as e:
                logger.warning(f"Could not load embedding model: {e}")
    
    def embed(self, text: str) -> Optional[List[float]]:
        if self.model and text:
            try:
                return self.model.encode(text).tolist()
            except:
                return None
        return None
