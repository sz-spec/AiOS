"""
Caching System
==============
Multi-backend caching for AI responses and generated code.
"""

import hashlib
import json

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Dict, Generic, TypeVar

from .errors import CacheError, get_logger

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """Represents a cached item."""

    key: str
    value: T
    created_at: float
    expires_at: float
    hit_count: int = 0
    metadata: Dict[str, Any] = None

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> Dict:
        return {
            "key": self.key,
            "value": self.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "hit_count": self.hit_count,
            "metadata": self.metadata,
        }


class CacheBackend(ABC):
    """Abstract base class for cache backends."""

    @abstractmethod
    def get(self, key: str) -> Optional[CacheEntry]:
        """Get item from cache."""
        pass

    @abstractmethod
    def set(self, entry: CacheEntry) -> bool:
        """Store item in cache."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete item from cache."""
        pass

    @abstractmethod
    def clear(self) -> bool:
        """Clear all items from cache."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        pass

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        pass


class MemoryCache(CacheBackend):
    """In-memory cache backend."""

    def __init__(self, max_size: int = 1000):
        self._cache: Dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[CacheEntry]:
        entry = self._cache.get(key)

        if entry is None:
            self._misses += 1
            return None

        if entry.is_expired():
            self.delete(key)
            self._misses += 1
            return None

        entry.hit_count += 1
        self._hits += 1
        return entry

    def set(self, entry: CacheEntry) -> bool:
        # Evict oldest if at capacity
        if len(self._cache) >= self._max_size:
            self._evict_oldest()

        self._cache[entry.key] = entry
        return True

    def delete(self, key: str) -> bool:
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> bool:
        self._cache.clear()
        return True

    def exists(self, key: str) -> bool:
        entry = self._cache.get(key)
        return entry is not None and not entry.is_expired()

    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "backend": "memory",
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0,
        }

    def _evict_oldest(self):
        """Remove oldest entry based on creation time."""
        if not self._cache:
            return

        oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].created_at)
        del self._cache[oldest_key]


class FileCache(CacheBackend):
    """File-based cache backend."""

    def __init__(self, cache_dir: Path, max_size: int = 1000):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_size = max_size
        self._index_file = self._cache_dir / "_index.json"
        self._index = self._load_index()

    def _load_index(self) -> Dict[str, Dict]:
        """Load cache index from file."""
        if self._index_file.exists():
            try:
                with open(self._index_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_index(self):
        """Save cache index to file."""
        with open(self._index_file, "w") as f:
            json.dump(self._index, f)

    def _get_file_path(self, key: str) -> Path:
        """Get file path for a cache key."""
        return self._cache_dir / f"{key}.json"

    def get(self, key: str) -> Optional[CacheEntry]:
        if key not in self._index:
            return None

        file_path = self._get_file_path(key)
        if not file_path.exists():
            del self._index[key]
            self._save_index()
            return None

        try:
            with open(file_path, "r") as f:
                data = json.load(f)
            entry = CacheEntry(**data)

            if entry.is_expired():
                self.delete(key)
                return None

            entry.hit_count += 1
            self._index[key]["hits"] = entry.hit_count
            self._save_index()

            return entry
        except Exception:
            return None

    def set(self, entry: CacheEntry) -> bool:
        # Evict if at capacity
        if len(self._index) >= self._max_size:
            self._evict_oldest()

        file_path = self._get_file_path(entry.key)

        try:
            with open(file_path, "w") as f:
                json.dump(entry.to_dict(), f)

            self._index[entry.key] = {
                "created_at": entry.created_at,
                "expires_at": entry.expires_at,
                "hits": entry.hit_count,
            }
            self._save_index()
            return True
        except Exception:
            return False

    def delete(self, key: str) -> bool:
        file_path = self._get_file_path(key)

        if file_path.exists():
            file_path.unlink()

        if key in self._index:
            del self._index[key]
            self._save_index()
            return True

        return False

    def clear(self) -> bool:
        for key in list(self._index.keys()):
            self.delete(key)
        return True

    def exists(self, key: str) -> bool:
        if key not in self._index:
            return False

        meta = self._index[key]
        if time.time() > meta.get("expires_at", 0):
            self.delete(key)
            return False

        return self._get_file_path(key).exists()

    def stats(self) -> Dict[str, Any]:
        total_hits = sum(m.get("hits", 0) for m in self._index.values())
        return {
            "backend": "file",
            "size": len(self._index),
            "max_size": self._max_size,
            "cache_dir": str(self._cache_dir),
            "total_hits": total_hits,
        }

    def _evict_oldest(self):
        """Remove oldest entry."""
        if not self._index:
            return

        oldest_key = min(self._index.keys(), key=lambda k: self._index[k]["created_at"])
        self.delete(oldest_key)


class RedisCache(CacheBackend):
    """Redis cache backend."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        try:
            import redis

            self._client = redis.from_url(redis_url)
            self._client.ping()  # Test connection
        except ImportError:
            raise CacheError("Redis package not installed", "init")
        except Exception as e:
            raise CacheError(f"Failed to connect to Redis: {e}", "init")

    def get(self, key: str) -> Optional[CacheEntry]:
        try:
            data = self._client.get(key)
            if data is None:
                return None

            entry = CacheEntry(**json.loads(data))

            # Increment hit count
            entry.hit_count += 1
            self._client.set(
                key, json.dumps(entry.to_dict()), ex=int(entry.expires_at - time.time())
            )

            return entry
        except Exception:
            return None

    def set(self, entry: CacheEntry) -> bool:
        try:
            ttl = int(entry.expires_at - time.time())
            if ttl <= 0:
                return False

            self._client.set(entry.key, json.dumps(entry.to_dict()), ex=ttl)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> bool:
        try:
            return self._client.delete(key) > 0
        except Exception:
            return False

    def clear(self) -> bool:
        try:
            self._client.flushdb()
            return True
        except Exception:
            return False

    def exists(self, key: str) -> bool:
        try:
            return self._client.exists(key) > 0
        except Exception:
            return False

    def stats(self) -> Dict[str, Any]:
        try:
            info = self._client.info()
            return {
                "backend": "redis",
                "keys": self._client.dbsize(),
                "memory_used": info.get("used_memory_human"),
                "connected_clients": info.get("connected_clients"),
            }
        except Exception:
            return {"backend": "redis", "error": "Unable to get stats"}


class Cache:
    """Main cache interface with key generation and statistics."""

    def __init__(
        self, backend: str = "memory", ttl: int = 3600, max_size: int = 1000, **kwargs
    ):
        self.logger = get_logger()
        self.ttl = ttl
        self._backend = self._create_backend(backend, max_size, **kwargs)

    def _create_backend(self, backend: str, max_size: int, **kwargs) -> CacheBackend:
        """Create appropriate cache backend."""
        if backend == "memory":
            return MemoryCache(max_size=max_size)
        elif backend == "file":
            return FileCache(
                cache_dir=kwargs.get("file_path", ".cache/ai_cache"), max_size=max_size
            )
        elif backend == "redis":
            return RedisCache(
                redis_url=kwargs.get("redis_url", "redis://localhost:6379/0")
            )
        else:
            self.logger.warning(f"Unknown backend '{backend}', using memory cache")
            return MemoryCache(max_size=max_size)

    @staticmethod
    def generate_key(*args, **kwargs) -> str:
        """Generate a cache key from arguments."""
        key_data = json.dumps(
            {
                "args": [str(a) for a in args],
                "kwargs": {k: str(v) for k, v in sorted(kwargs.items())},
            },
            sort_keys=True,
        )

        return hashlib.sha256(key_data.encode()).hexdigest()[:32]

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        entry = self._backend.get(key)
        if entry:
            self.logger.debug(f"Cache hit for key: {key[:8]}...")
            return entry.value
        self.logger.debug(f"Cache miss for key: {key[:8]}...")
        return None

    def set(self, key: str, value: Any, ttl: int = None, metadata: Dict = None) -> bool:
        """Set value in cache."""
        current_time = time.time()
        entry = CacheEntry(
            key=key,
            value=value,
            created_at=current_time,
            expires_at=current_time + (ttl or self.ttl),
            metadata=metadata,
        )

        success = self._backend.set(entry)
        if success:
            self.logger.debug(f"Cached value for key: {key[:8]}...")
        return success

    def delete(self, key: str) -> bool:
        """Delete value from cache."""
        return self._backend.delete(key)

    def clear(self) -> bool:
        """Clear all cached values."""
        return self._backend.clear()

    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        return self._backend.exists(key)

    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return self._backend.stats()

    def cached(self, ttl: int = None, key_prefix: str = ""):
        """Decorator to cache function results."""

        def decorator(func):
            from functools import wraps

            @wraps(func)
            def wrapper(*args, **kwargs):
                # Generate cache key
                key = key_prefix + self.generate_key(func.__name__, *args, **kwargs)

                # Try to get from cache
                cached_value = self.get(key)
                if cached_value is not None:
                    return cached_value

                # Execute function and cache result
                result = func(*args, **kwargs)
                self.set(key, result, ttl=ttl)

                return result

            return wrapper

        return decorator


# Global cache instance
_cache: Optional[Cache] = None


def get_cache(
    backend: str = "memory", ttl: int = 3600, max_size: int = 1000, **kwargs
) -> Cache:
    """Get or create global cache instance."""
    global _cache
    if _cache is None:
        _cache = Cache(backend=backend, ttl=ttl, max_size=max_size, **kwargs)
    return _cache
