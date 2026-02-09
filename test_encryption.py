#!/usr/bin/env python3
"""
Test script to verify ClawBrain encryption functionality
"""

import os
import sys
import tempfile
from pathlib import Path

# Add parent directory to path to import clawbrain
sys.path.insert(0, str(Path(__file__).parent))

try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    print("❌ cryptography library not installed")
    print("   Install with: pip install cryptography")
    sys.exit(1)

from clawbrain import Brain


def test_encryption():
    """Test encryption and decryption of secrets"""
    print("🧪 Testing ClawBrain Encryption\n")
    
    # Create a temporary database for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_brain.db")
        
        print(f"📁 Using temporary database: {db_path}")
        
        # Initialize Brain with test database
        brain = Brain({
            "storage_backend": "sqlite",
            "sqlite_path": db_path
        })
        
        print(f"✅ Brain initialized with {brain.storage_backend} storage")
        
        # Check if encryption is available
        if not brain._cipher:
            print("❌ Encryption not available!")
            return False
        
        print("✅ Encryption cipher initialized")
        
        # Test 1: Store encrypted secret
        print("\n📝 Test 1: Storing encrypted secret...")
        test_secret = "sk-1234567890abcdef-test-api-key"
        
        memory = brain.remember(
            agent_id="test_agent",
            memory_type="secret",
            content=test_secret,
            key="test_api_key"
        )
        
        print(f"   Stored memory ID: {memory.id}")
        print(f"   Content encrypted: {memory.content_encrypted}")
        print(f"   Encrypted content (truncated): {memory.content[:50]}...")
        
        if not memory.content_encrypted:
            print("❌ Memory was not encrypted!")
            return False
        
        if memory.content == test_secret:
            print("❌ Content is not encrypted (matches original)!")
            return False
        
        print("✅ Secret stored with encryption")
        
        # Test 2: Retrieve and decrypt secret
        print("\n📝 Test 2: Retrieving and decrypting secret...")
        
        secrets = brain.recall(agent_id="test_agent", memory_type="secret")
        
        if not secrets:
            print("❌ No secrets found!")
            return False
        
        retrieved_secret = secrets[0]
        print(f"   Retrieved memory ID: {retrieved_secret.id}")
        print(f"   Content encrypted flag: {retrieved_secret.content_encrypted}")
        print(f"   Decrypted content: {retrieved_secret.content}")
        
        if retrieved_secret.content != test_secret:
            print(f"❌ Decrypted content doesn't match!")
            print(f"   Expected: {test_secret}")
            print(f"   Got: {retrieved_secret.content}")
            return False
        
        print("✅ Secret retrieved and decrypted successfully")
        
        # Test 3: Verify regular memories are not encrypted
        print("\n📝 Test 3: Verifying regular memories are not encrypted...")
        
        regular_memory = brain.remember(
            agent_id="test_agent",
            memory_type="preference",
            content="User prefers dark mode",
            key="ui_preference"
        )
        
        if regular_memory.content_encrypted:
            print("❌ Regular memory was incorrectly encrypted!")
            return False
        
        if regular_memory.content != "User prefers dark mode":
            print("❌ Regular memory content was modified!")
            return False
        
        print("✅ Regular memories remain unencrypted")
        
        # Test 4: Verify encryption key persistence
        print("\n📝 Test 4: Verifying encryption key persistence...")
        
        key_path = Path(tmpdir) / ".brain_key"
        if not key_path.exists():
            print("❌ Encryption key file not created!")
            return False
        
        # Check file permissions (Unix-like systems only)
        if hasattr(os, 'stat'):
            import stat
            key_stat = key_path.stat()
            mode = key_stat.st_mode
            # Check if only owner has read/write permissions
            if mode & stat.S_IRWXG or mode & stat.S_IRWXO:
                print(f"⚠️  Warning: Key file has overly permissive permissions: {oct(mode)}")
            else:
                print(f"✅ Key file has secure permissions: {oct(mode)}")
        
        print("✅ Encryption key persisted correctly")
        
        # Clean up
        brain.close()
        
        return True


def main():
    """Run all tests"""
    try:
        success = test_encryption()
        
        print("\n" + "="*50)
        if success:
            print("✅ All encryption tests passed!")
            print("\n💡 ClawBrain encryption is working correctly.")
            print("   You can now safely store secrets using memory_type='secret'")
            return 0
        else:
            print("❌ Encryption tests failed!")
            return 1
            
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
