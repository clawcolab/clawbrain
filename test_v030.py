#!/usr/bin/env python3
"""
Integration tests for ClawBrain v0.3.0 features.

Tests:
1. Schema migration (new columns exist)
2. Memory dataclass new fields
3. remember() with new fields
4. Hybrid retrieval scoring
5. recall() with explain=True
6. recall() with filters (memory_kind, scope, since, etc.)
7. Deduplication on ingest
8. Memory merge
9. Rule-based extraction from conversations
10. ingest_conversation() end-to-end
11. consolidate_session()
12. Audit logging
13. stats()
14. Access tracking
15. Backward compatibility (old-style calls still work)
"""

import os
import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta

# Use a temp database for testing
TEST_DB = os.path.join(tempfile.gettempdir(), "clawbrain_test_v030.db")

# Clean up any prior test DB
if os.path.exists(TEST_DB):
    os.unlink(TEST_DB)

os.environ["BRAIN_SQLITE_PATH"] = TEST_DB
os.environ["BRAIN_STORAGE"] = "sqlite"

from clawbrain import Brain, Memory, ScoredMemory, VALID_MEMORY_KINDS, VALID_DURABILITIES, VALID_SCOPES


class TestSchemaAndDataclass(unittest.TestCase):
    """Test schema migration and Memory dataclass."""

    def setUp(self):
        self.brain = Brain({"sqlite_path": TEST_DB, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_memory_dataclass_has_new_fields(self):
        """Memory dataclass should have all v0.3.0 fields."""
        m = Memory(
            id="test", agent_id="a", memory_type="fact", key="k", content="c",
            content_encrypted=False, summary="s", keywords=[], tags=[],
            importance=5, linked_to=None, source=None, embedding=None,
            created_at="now", updated_at="now",
        )
        self.assertEqual(m.memory_kind, "fact")
        self.assertEqual(m.confidence, 1.0)
        self.assertEqual(m.durability, "long_term")
        self.assertEqual(m.scope, "private")
        self.assertEqual(m.scope_id, "")
        self.assertEqual(m.access_count, 0)
        self.assertIsNone(m.last_accessed_at)
        self.assertEqual(m.created_by_agent, "")

    def test_schema_has_new_columns(self):
        """Database should have all new columns after migration."""
        with self.brain._get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(memories)")
            columns = {row["name"] for row in cursor.fetchall()}

        expected = {"memory_kind", "confidence", "durability", "scope", "scope_id",
                    "access_count", "last_accessed_at", "created_by_agent"}
        for col in expected:
            self.assertIn(col, columns, f"Missing column: {col}")

    def test_memory_events_table_exists(self):
        """memory_events audit log table should exist."""
        with self.brain._get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(memory_events)")
            columns = {row["name"] for row in cursor.fetchall()}

        expected = {"id", "memory_id", "event_type", "details", "actor", "created_at"}
        for col in expected:
            self.assertIn(col, columns, f"Missing column in memory_events: {col}")


class TestRememberWithNewFields(unittest.TestCase):
    """Test remember() with v0.3.0 fields."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_remember.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_remember_with_kind_and_scope(self):
        """remember() should store memory_kind, scope, confidence, durability."""
        mem = self.brain.remember(
            agent_id="test_agent",
            memory_type="learned",
            content="User prefers Python over JavaScript for backend",
            memory_kind="preference",
            confidence=0.9,
            scope="shared",
            scope_id="team_backend",
            durability="long_term",
            deduplicate=False,
        )
        self.assertEqual(mem.memory_kind, "preference")
        self.assertEqual(mem.confidence, 0.9)
        self.assertEqual(mem.scope, "shared")
        self.assertEqual(mem.scope_id, "team_backend")
        self.assertEqual(mem.durability, "long_term")

    def test_remember_validates_memory_kind(self):
        """Invalid memory_kind should default to 'fact'."""
        mem = self.brain.remember(
            agent_id="test_agent",
            memory_type="learned",
            content="Some content here",
            memory_kind="invalid_kind",
            deduplicate=False,
        )
        self.assertEqual(mem.memory_kind, "fact")

    def test_remember_clamps_confidence(self):
        """Confidence should be clamped to 0.0-1.0."""
        mem = self.brain.remember(
            agent_id="test_agent",
            memory_type="learned",
            content="Overclamped confidence test",
            confidence=5.0,
            deduplicate=False,
        )
        self.assertEqual(mem.confidence, 1.0)

    def test_remember_creates_audit_event(self):
        """remember() should create an audit log entry."""
        mem = self.brain.remember(
            agent_id="test_agent",
            memory_type="learned",
            content="Audit log test memory",
            deduplicate=False,
        )
        events = self.brain.get_audit_log(memory_id=mem.id)
        self.assertTrue(len(events) > 0)
        self.assertEqual(events[0]["event_type"], "created")


class TestHybridRetrieval(unittest.TestCase):
    """Test hybrid scoring and recall() with new features."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_retrieval.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

        # Seed test memories
        self.brain.remember("agent1", "learned", "User prefers Python for backend development",
                            memory_kind="preference", importance=8, confidence=0.9,
                            deduplicate=False, key="pref_python")
        self.brain.remember("agent1", "learned", "Project uses PostgreSQL database",
                            memory_kind="fact", importance=7, confidence=1.0,
                            deduplicate=False, key="fact_postgres")
        self.brain.remember("agent1", "learned", "Deploy using Docker containers on AWS",
                            memory_kind="procedure", importance=6, confidence=0.8,
                            scope="shared", scope_id="team_devops",
                            deduplicate=False, key="proc_deploy")
        self.brain.remember("agent1", "learned", "Never commit API keys to git",
                            memory_kind="constraint", importance=9, confidence=1.0,
                            deduplicate=False, key="constraint_keys")
        self.brain.remember("agent1", "learned", "User likes concise explanations with examples",
                            memory_kind="preference", importance=7, confidence=0.85,
                            deduplicate=False, key="pref_concise")

    def tearDown(self):
        self.brain.close()

    def test_recall_with_query_returns_scored_results(self):
        """recall() with a query should use hybrid scoring."""
        results = self.brain.recall(agent_id="agent1", query="Python programming", limit=3)
        self.assertTrue(len(results) > 0)
        # Python preference should score highest
        self.assertIn("python", results[0].content.lower())

    def test_recall_explain_mode(self):
        """recall(explain=True) should return score breakdowns."""
        results = self.brain.recall(
            agent_id="agent1",
            query="database",
            limit=3,
            explain=True,
        )
        self.assertTrue(len(results) > 0)
        first = results[0]
        self.assertIn("memory", first)
        self.assertIn("score", first)
        self.assertIn("breakdown", first)
        self.assertIn("keyword", first["breakdown"])
        self.assertIn("recency", first["breakdown"])
        self.assertIn("importance", first["breakdown"])
        self.assertIn("reason", first)

    def test_recall_filter_by_memory_kind(self):
        """recall() should filter by memory_kind."""
        prefs = self.brain.recall(agent_id="agent1", memory_kind="preference")
        for m in prefs:
            self.assertEqual(m.memory_kind, "preference")

    def test_recall_filter_by_scope(self):
        """recall() should filter by scope."""
        shared = self.brain.recall(agent_id="agent1", scope="shared")
        for m in shared:
            self.assertEqual(m.scope, "shared")
        self.assertTrue(len(shared) > 0)

    def test_recall_filter_by_min_confidence(self):
        """recall() should filter by minimum confidence."""
        high_conf = self.brain.recall(agent_id="agent1", min_confidence=0.9)
        for m in high_conf:
            self.assertGreaterEqual(m.confidence, 0.9)

    def test_recall_no_query_returns_by_importance(self):
        """recall() without query should return by importance (backward compat)."""
        results = self.brain.recall(agent_id="agent1", limit=3)
        self.assertTrue(len(results) > 0)
        # Should be sorted by importance descending
        importances = [m.importance for m in results]
        self.assertEqual(importances, sorted(importances, reverse=True))

    def test_recall_custom_weights(self):
        """recall() should accept custom scoring weights."""
        # Weight heavily toward recency
        results = self.brain.recall(
            agent_id="agent1",
            query="Python",
            weights={"semantic": 0, "keyword": 0, "recency": 1.0, "importance": 0, "confidence": 0},
            limit=5,
        )
        self.assertTrue(len(results) > 0)


class TestDeduplication(unittest.TestCase):
    """Test memory deduplication on ingest."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_dedup.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_exact_duplicate_merges(self):
        """Storing the same content twice should merge, not create duplicate."""
        mem1 = self.brain.remember("agent1", "learned", "User prefers dark mode",
                                   memory_kind="preference", deduplicate=True)
        mem2 = self.brain.remember("agent1", "learned", "User prefers dark mode",
                                   memory_kind="preference", deduplicate=True)
        # Should be the same memory (merged)
        self.assertEqual(mem1.id, mem2.id)

    def test_different_kind_not_merged(self):
        """Memories with different kinds should not merge even if similar."""
        mem1 = self.brain.remember("agent1", "learned", "Python is the primary language",
                                   memory_kind="fact", deduplicate=True)
        mem2 = self.brain.remember("agent1", "learned", "Python is the primary language",
                                   memory_kind="preference", deduplicate=True)
        # Different kinds should create separate memories
        self.assertNotEqual(mem1.id, mem2.id)

    def test_merge_bumps_confidence(self):
        """Merging should pick the higher confidence value."""
        mem1 = self.brain.remember("agent1", "learned", "User works remotely",
                                   memory_kind="fact", confidence=0.6, deduplicate=True)
        mem2 = self.brain.remember("agent1", "learned", "User works remotely",
                                   memory_kind="fact", confidence=0.9, deduplicate=True)
        self.assertGreaterEqual(mem2.confidence, 0.9)


class TestConversationIngestion(unittest.TestCase):
    """Test conversation ingestion and rule-based extraction."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_ingest.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_rule_based_preference_extraction(self):
        """Rule-based extraction should detect preferences."""
        messages = [
            {"role": "user", "content": "I prefer Python over JavaScript for backend work."},
            {"role": "assistant", "content": "Python is great for backend development!"},
        ]
        extracted = self.brain.ingest_conversation("agent1", "user1", messages)
        pref_memories = [m for m in extracted if m.memory_kind == "preference"]
        self.assertTrue(len(pref_memories) > 0, "Should extract at least one preference")

    def test_rule_based_fact_extraction(self):
        """Rule-based extraction should detect facts."""
        messages = [
            {"role": "user", "content": "I work at Google on the Cloud team."},
            {"role": "assistant", "content": "That's exciting!"},
        ]
        extracted = self.brain.ingest_conversation("agent1", "user1", messages)
        fact_memories = [m for m in extracted if m.memory_kind == "fact"]
        self.assertTrue(len(fact_memories) > 0, "Should extract at least one fact")

    def test_rule_based_task_extraction(self):
        """Rule-based extraction should detect tasks."""
        messages = [
            {"role": "user", "content": "Remember to update the API docs before Friday."},
            {"role": "assistant", "content": "I'll note that down."},
        ]
        extracted = self.brain.ingest_conversation("agent1", "user1", messages)
        task_memories = [m for m in extracted if m.memory_kind == "task"]
        self.assertTrue(len(task_memories) > 0, "Should extract at least one task")

    def test_ingest_deduplicates_within_batch(self):
        """Ingestion should not create duplicate memories from repeated info."""
        messages = [
            {"role": "user", "content": "I prefer dark mode."},
            {"role": "user", "content": "I really prefer dark mode for everything."},
        ]
        extracted = self.brain.ingest_conversation("agent1", "user1", messages)
        # May extract from both messages but dedup should handle overlap
        all_memories = self.brain.recall(agent_id="agent1")
        # Should have reasonable count (not duplicated wildly)
        self.assertLessEqual(len(all_memories), 5)

    def test_ingest_empty_messages(self):
        """Ingesting empty messages should return empty list."""
        result = self.brain.ingest_conversation("agent1", "user1", [])
        self.assertEqual(result, [])


class TestSessionConsolidation(unittest.TestCase):
    """Test session consolidation."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_consolidate.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_consolidate_creates_summary(self):
        """consolidate_session() should create a summary memory."""
        messages = [
            {"role": "user", "content": "I need help debugging my PostgreSQL connection timeout issues."},
            {"role": "assistant", "content": "Let's check your connection pooling configuration."},
            {"role": "user", "content": "I prefer using PgBouncer for connection pooling."},
            {"role": "assistant", "content": "Here's how to configure PgBouncer..."},
            {"role": "user", "content": "That fixed it, thanks!"},
        ]
        report = self.brain.consolidate_session("agent1", "user1", messages, session_id="test_session")

        self.assertIn("summary", report)
        self.assertTrue(len(report["summary"]) > 0)
        self.assertIn("extracted", report)
        self.assertIn("stats", report)
        self.assertEqual(report["stats"]["session_id"], "test_session")
        self.assertGreater(report["stats"]["memories_extracted"], 0)

    def test_consolidate_extracts_memories(self):
        """consolidate_session() should extract memories from conversation."""
        messages = [
            {"role": "user", "content": "I work at Stripe on the payments team."},
            {"role": "assistant", "content": "That's interesting! How can I help?"},
            {"role": "user", "content": "I prefer TypeScript for our microservices."},
        ]
        report = self.brain.consolidate_session("agent1", "user1", messages)

        extracted_kinds = {m.memory_kind for m in report["extracted"]}
        self.assertTrue(len(report["extracted"]) > 0)

    def test_consolidate_stores_summary_as_episode(self):
        """The session summary should be stored as an episode memory."""
        messages = [
            {"role": "user", "content": "Can you help me set up CI/CD for our project?"},
            {"role": "assistant", "content": "Sure! What CI provider are you using?"},
        ]
        report = self.brain.consolidate_session("agent1", "user1", messages)

        # Verify the summary was stored
        summaries = self.brain.recall(agent_id="agent1", memory_kind="summary")
        self.assertTrue(len(summaries) > 0)


class TestAuditLog(unittest.TestCase):
    """Test audit logging."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_audit.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_remember_logs_created_event(self):
        """remember() should log a 'created' event."""
        mem = self.brain.remember("agent1", "learned", "Audit test", deduplicate=False)
        events = self.brain.get_audit_log(memory_id=mem.id, event_type="created")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "created")

    def test_forget_logs_deleted_event(self):
        """forget() should log a 'deleted' event."""
        mem = self.brain.remember("agent1", "learned", "To be forgotten", deduplicate=False)
        self.brain.forget(mem.id)
        events = self.brain.get_audit_log(memory_id=mem.id, event_type="deleted")
        self.assertEqual(len(events), 1)

    def test_correct_logs_corrected_event(self):
        """correct() should log a 'corrected' event."""
        mem = self.brain.remember("agent1", "learned", "Original content", deduplicate=False)
        self.brain.correct(mem.id, "Corrected content")
        events = self.brain.get_audit_log(memory_id=mem.id, event_type="corrected")
        self.assertEqual(len(events), 1)

    def test_audit_log_filter_by_event_type(self):
        """get_audit_log() should filter by event type."""
        self.brain.remember("agent1", "learned", "Mem A", deduplicate=False)
        self.brain.remember("agent1", "learned", "Mem B", deduplicate=False)
        events = self.brain.get_audit_log(event_type="created")
        for evt in events:
            self.assertEqual(evt["event_type"], "created")


class TestStats(unittest.TestCase):
    """Test stats() method."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_stats.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

        self.brain.remember("agent1", "learned", "Fact 1", memory_kind="fact",
                            scope="private", deduplicate=False)
        self.brain.remember("agent1", "learned", "Pref 1", memory_kind="preference",
                            scope="shared", scope_id="team1", deduplicate=False)
        self.brain.remember("agent1", "learned", "Task 1", memory_kind="task",
                            durability="short_term", deduplicate=False)

    def tearDown(self):
        self.brain.close()

    def test_stats_returns_totals(self):
        """stats() should return correct totals."""
        s = self.brain.stats()
        self.assertGreaterEqual(s["total_memories"], 3)

    def test_stats_by_kind(self):
        """stats() should break down by memory_kind."""
        s = self.brain.stats()
        self.assertIn("fact", s["by_kind"])
        self.assertIn("preference", s["by_kind"])
        self.assertIn("task", s["by_kind"])

    def test_stats_by_scope(self):
        """stats() should break down by scope."""
        s = self.brain.stats()
        self.assertIn("private", s["by_scope"])
        self.assertIn("shared", s["by_scope"])

    def test_stats_has_averages(self):
        """stats() should include avg_importance and avg_confidence."""
        s = self.brain.stats()
        self.assertGreater(s["avg_importance"], 0)
        self.assertGreater(s["avg_confidence"], 0)


class TestAccessTracking(unittest.TestCase):
    """Test access_count and last_accessed_at tracking."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_access.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_recall_increments_access_count(self):
        """recall() should increment access_count for returned memories."""
        mem = self.brain.remember("agent1", "learned", "Access tracking test content here",
                                  memory_kind="fact", deduplicate=False)

        # Recall should trigger access tracking
        results = self.brain.recall(agent_id="agent1", query="access tracking test")
        self.assertTrue(len(results) > 0)

        # Check that access_count was incremented in the database
        with self.brain._get_cursor() as cursor:
            cursor.execute("SELECT access_count, last_accessed_at FROM memories WHERE id = ?", (mem.id,))
            row = cursor.fetchone()
            self.assertGreater(row["access_count"], 0)
            self.assertIsNotNone(row["last_accessed_at"])


class TestBackwardCompatibility(unittest.TestCase):
    """Test that v0.2.0 style calls still work."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_compat.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_old_style_remember_works(self):
        """Old-style remember() calls should still work."""
        mem = self.brain.remember(
            agent_id="test",
            memory_type="knowledge",
            content="Simple old-style memory",
            importance=5,
        )
        self.assertIsNotNone(mem.id)
        self.assertEqual(mem.memory_kind, "fact")  # default
        self.assertEqual(mem.scope, "private")  # default

    def test_old_style_recall_works(self):
        """Old-style recall() calls should still work."""
        self.brain.remember("test", "knowledge", "Recall compat test", deduplicate=False)
        results = self.brain.recall(agent_id="test", memory_type="knowledge", limit=5)
        self.assertTrue(len(results) > 0)

    def test_get_full_context_works(self):
        """get_full_context() should still return expected structure."""
        self.brain.remember("assistant", "knowledge", "Context test memory", deduplicate=False)
        ctx = self.brain.get_full_context(
            session_key="test_session",
            user_id="user1",
            agent_id="assistant",
            message="Hello, how are you?",
        )
        self.assertIn("user", ctx)
        self.assertIn("conversation", ctx)
        self.assertIn("memories", ctx)
        self.assertIn("response_guidance", ctx)


class TestTraitsRedesign(unittest.TestCase):
    """Test the redesigned traits system: bidirectional, decay, per-user, new traits."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_traits.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_default_traits_include_new_traits(self):
        """Soul should include conciseness, formality, directness traits."""
        soul = self.brain.get_soul("agent1")
        for trait in ["humor", "empathy", "curiosity", "creativity", "helpfulness",
                      "honesty", "conciseness", "formality", "directness"]:
            self.assertIn(trait, soul["traits"], f"Missing trait: {trait}")

    def test_negative_signals_decrease_traits(self):
        """Negative signals should decrease trait values below 0.5."""
        # First get baseline
        soul = self.brain.get_soul("agent1")
        initial_humor = soul["traits"]["humor"]

        # Apply negative signal repeatedly
        for _ in range(20):
            self.brain.evolve_traits("agent1", {"humor": -0.8})

        soul = self.brain.get_soul("agent1")
        self.assertLess(soul["traits"]["humor"], initial_humor,
                        "Negative signal should decrease humor trait")

    def test_bidirectional_signal_detection(self):
        """analyze_interaction_for_traits should detect both positive and negative signals."""
        # Positive humor signal
        pos_signals = self.brain.analyze_interaction_for_traits("haha that's so funny lol")
        self.assertGreater(pos_signals.get("humor", 0), 0, "Should detect positive humor")

        # Negative humor signal
        neg_signals = self.brain.analyze_interaction_for_traits("stop joking, be serious please")
        self.assertLess(neg_signals.get("humor", 0), 0, "Should detect negative humor")

    def test_conciseness_signal_detection(self):
        """Should detect conciseness preference signals."""
        # User wants brevity
        signals = self.brain.analyze_interaction_for_traits("too long, just give me the tl;dr")
        self.assertGreater(signals.get("conciseness", 0), 0)

        # User wants detail
        signals = self.brain.analyze_interaction_for_traits("can you elaborate more? need more detail")
        self.assertLess(signals.get("conciseness", 0), 0)

    def test_formality_signal_detection(self):
        """Should detect formality preference signals."""
        # User wants formal
        signals = self.brain.analyze_interaction_for_traits("Please keep it professional")
        self.assertGreater(signals.get("formality", 0), 0)

        # User wants casual
        signals = self.brain.analyze_interaction_for_traits("chill dude, no need to be formal")
        self.assertLess(signals.get("formality", 0), 0)

    def test_per_user_traits(self):
        """evolve_traits with user_id should create per-user trait overrides."""
        # Evolve traits for user A (likes humor)
        for _ in range(10):
            self.brain.evolve_traits("agent1", {"humor": 0.8}, user_id="userA")

        # Evolve traits for user B (no humor please)
        for _ in range(10):
            self.brain.evolve_traits("agent1", {"humor": -0.8}, user_id="userB")

        # Get merged traits for each user
        soul_a = self.brain.get_soul("agent1", user_id="userA")
        soul_b = self.brain.get_soul("agent1", user_id="userB")

        self.assertGreater(soul_a["traits"]["humor"], soul_b["traits"]["humor"],
                           "User A should have higher humor than User B")

    def test_trait_decay_toward_neutral(self):
        """Traits should decay toward 0.5 over time."""
        # Set a trait high
        self.brain.evolve_traits("agent1", {"humor": 1.0})
        soul = self.brain.get_soul("agent1")

        # Simulate decay by manipulating last_decay_at in the past
        past = (datetime.now() - timedelta(days=60)).isoformat()
        with self.brain._get_cursor() as cursor:
            cursor.execute("UPDATE souls SET last_decay_at = ? WHERE agent_id = ?",
                           (past, "agent1"))

        # Get soul again — should trigger decay
        soul = self.brain.get_soul("agent1")
        # After 60 days (2x half-life of 30 days), trait should be noticeably closer to 0.5
        self.assertLess(soul["traits"]["humor"], 0.9,
                        "Humor trait should have decayed toward 0.5")

    def test_trait_influenced_guidance_new_traits(self):
        """Guidance should include conciseness, formality, directness effects."""
        # Set high conciseness and formality
        for _ in range(30):
            self.brain.evolve_traits("agent1", {"conciseness": 1.0, "formality": 1.0, "directness": 1.0})

        guidance = self.brain.get_trait_influenced_guidance("agent1")

        # Check new guidance keys exist
        self.assertIn("verbosity", guidance)
        self.assertIn("formality", guidance)

    def test_trait_biased_weights(self):
        """get_trait_biased_weights should return weight multipliers."""
        weights = self.brain.get_trait_biased_weights("agent1")
        self.assertIn("preference", weights)
        self.assertIn("task", weights)
        self.assertIn("summary", weights)
        self.assertIn("fact", weights)

    def test_ingest_evolves_traits(self):
        """ingest_conversation should auto-evolve traits from messages."""
        soul_before = self.brain.get_soul("agent1")
        initial_count = soul_before["interaction_count"]

        messages = [
            {"role": "user", "content": "haha that's so funny lol, thanks for being helpful!"},
            {"role": "assistant", "content": "Glad I could help!"},
            {"role": "user", "content": "keep it short please, tl;dr"},
        ]
        self.brain.ingest_conversation("agent1", "user1", messages)

        soul_after = self.brain.get_soul("agent1")
        self.assertGreater(soul_after["interaction_count"], initial_count,
                           "Interaction count should increase after ingest")

    def test_soul_user_traits_table_exists(self):
        """soul_user_traits table should exist after initialization."""
        with self.brain._get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(soul_user_traits)")
            columns = {row["name"] for row in cursor.fetchall()}

        expected = {"agent_id", "user_id", "traits", "interaction_count",
                    "last_decay_at", "created_at", "updated_at"}
        for col in expected:
            self.assertIn(col, columns, f"Missing column: {col}")


class TestBondEvolution(unittest.TestCase):
    """Test the bond/relationship evolution system."""

    def setUp(self):
        db = os.path.join(tempfile.gettempdir(), "clawbrain_test_bonds.db")
        if os.path.exists(db):
            os.unlink(db)
        self.brain = Brain({"sqlite_path": db, "storage_backend": "sqlite"})

    def tearDown(self):
        self.brain.close()

    def test_initial_bond_creation(self):
        """First interaction should create a bond."""
        bond = self.brain.update_bond("user1", "agent1",
                                       [{"role": "user", "content": "Hello!"}])
        self.assertIsNotNone(bond)
        self.assertEqual(bond["user_id"], "user1")
        self.assertEqual(bond["total_interactions"], 1)
        self.assertGreater(bond["level"], 0)
        self.assertIn("stage", bond)

    def test_bond_grows_with_interactions(self):
        """Bond level should increase with repeated positive interactions."""
        messages = [{"role": "user", "content": "Thanks, that was really helpful!"}]

        for i in range(20):
            bond = self.brain.update_bond("user1", "agent1", messages)

        self.assertGreater(bond["level"], 0.2,
                           "Bond level should grow with interactions")
        self.assertEqual(bond["total_interactions"], 20)

    def test_bond_milestones(self):
        """Bond should achieve milestones at interaction thresholds."""
        messages = [{"role": "user", "content": "Hello"}]

        bond = None
        for i in range(11):
            bond = self.brain.update_bond("user1", "agent1", messages)

        # Should have first conversation (1) and getting acquainted (10) milestones
        milestone_names = [m["name"] for m in bond["milestones"]]
        self.assertIn("First conversation", milestone_names)
        self.assertIn("Getting acquainted", milestone_names)

    def test_bond_sentiment_from_messages(self):
        """Bond should respond to message sentiment."""
        # Positive interaction
        pos_bond = self.brain.update_bond("user_pos", "agent1",
                                           [{"role": "user", "content": "Amazing, perfect, love it!"}])

        # Negative interaction
        neg_bond = self.brain.update_bond("user_neg", "agent1",
                                           [{"role": "user", "content": "Terrible, awful, hate this"}])

        self.assertGreater(pos_bond["score"], neg_bond["score"],
                           "Positive sentiment should yield higher score")

    def test_get_bond_returns_none_for_unknown_user(self):
        """get_bond() should return None for unknown users."""
        bond = self.brain.get_bond("nonexistent_user")
        self.assertIsNone(bond)

    def test_get_bond_returns_stage(self):
        """get_bond() should include relationship stage info."""
        self.brain.update_bond("user1", "agent1",
                                [{"role": "user", "content": "Hi"}])
        bond = self.brain.get_bond("user1")
        self.assertIn("stage", bond)
        self.assertIn("stage_description", bond)

    def test_bond_in_full_context(self):
        """get_full_context should include bond data."""
        # Create a bond first
        self.brain.update_bond("user1", "agent1",
                                [{"role": "user", "content": "Hello!"}])

        ctx = self.brain.get_full_context(
            session_key="test",
            user_id="user1",
            agent_id="agent1",
            message="How are you?",
        )
        self.assertIn("bond", ctx)
        self.assertIsNotNone(ctx["bond"])

    def test_ingest_updates_bond(self):
        """ingest_conversation should update bond for the user."""
        messages = [
            {"role": "user", "content": "I prefer Python for backend development."},
            {"role": "assistant", "content": "Python is great for backend!"},
        ]
        self.brain.ingest_conversation("agent1", "user1", messages)

        bond = self.brain.get_bond("user1")
        self.assertIsNotNone(bond, "Bond should be created during ingestion")
        self.assertGreater(bond["total_interactions"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
