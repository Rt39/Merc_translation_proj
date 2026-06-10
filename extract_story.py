"""Extract story data from downloaded asset bundles."""
import UnityPy
import os
import sys

CACHE_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64"
STORY_DIR = os.path.join(CACHE_DIR, "Story")
MASTER_DIR = os.path.join(CACHE_DIR, "StoryMasterData")
MASTERDATA_DIR = os.path.join(CACHE_DIR, "MasterData")

def analyze_bundle(bpath, label=""):
    """Analyze a single bundle and return info about its contents."""
    try:
        env = UnityPy.load(bpath)
        results = []
        for obj in env.objects:
            typ = obj.type.name
            if typ == "TextAsset":
                data = obj.read()
                name = getattr(data, 'm_Name', '') or getattr(data, 'name', '')
                script = getattr(data, 'm_Script', b'') or getattr(data, 'script', b'')
                if isinstance(script, str):
                    script = script.encode('utf-8', errors='replace')
                results.append(('TextAsset', name, len(script), script[:500]))
            elif typ == "MonoBehaviour":
                try:
                    data = obj.read()
                    # Try type tree first
                    if obj.serialized_type and obj.serialized_type.nodes:
                        tree = data.read_typetree()
                        results.append(('MonoBehaviour_typed', str(tree)[:300], obj.path_id, None))
                    else:
                        raw = obj.get_raw_data()
                        results.append(('MonoBehaviour_raw', f"PathID={obj.path_id}", len(raw), raw[:500] if raw else b''))
                except Exception as e:
                    raw = obj.get_raw_data()
                    results.append(('MonoBehaviour_raw', f"PathID={obj.path_id} err={e}", len(raw) if raw else 0, raw[:500] if raw else b''))
        return results
    except Exception as e:
        return [('Error', str(e), 0, b'')]


# Check a few Story bundles
print("=" * 60)
print("STORY BUNDLES (sample)")
print("=" * 60)

story_files = sorted(os.listdir(STORY_DIR))[:5]
for fname in story_files:
    fpath = os.path.join(STORY_DIR, fname)
    print(f"\n--- {fname} (size={os.path.getsize(fpath)}) ---")
    results = analyze_bundle(fpath)
    for typ, name, size, data in results:
        print(f"  Type: {typ}, Name/Info: {name}, Size: {size}")
        if data and isinstance(data, bytes):
            # Try decode as UTF-8
            try:
                text = data.decode('utf-8', errors='replace')
                print(f"  Text preview: {text[:300]}")
            except:
                print(f"  Raw hex: {data[:100].hex()}")
        elif data is None and isinstance(name, str):
            print(f"  TypeTree: {name[:400]}")

# Check StoryMasterData bundles
print("\n" + "=" * 60)
print("STORY MASTER DATA BUNDLES (sample)")
print("=" * 60)

master_files = sorted(os.listdir(MASTER_DIR))[:5]
for fname in master_files:
    fpath = os.path.join(MASTER_DIR, fname)
    print(f"\n--- {fname} (size={os.path.getsize(fpath)}) ---")
    results = analyze_bundle(fpath)
    for typ, name, size, data in results:
        print(f"  Type: {typ}, Name/Info: {name}, Size: {size}")
        if data and isinstance(data, bytes):
            try:
                text = data.decode('utf-8', errors='replace')
                print(f"  Text preview: {text[:300]}")
            except:
                print(f"  Raw hex: {data[:100].hex()}")
        elif data is None and isinstance(name, str):
            print(f"  TypeTree: {name[:400]}")

# Check MasterData bundles
print("\n" + "=" * 60)
print("MASTER DATA BUNDLES")
print("=" * 60)

md_files = sorted(os.listdir(MASTERDATA_DIR))
for fname in md_files[:5]:
    if not fname.endswith('.bundle'):
        continue
    fpath = os.path.join(MASTERDATA_DIR, fname)
    print(f"\n--- {fname} (size={os.path.getsize(fpath)}) ---")
    results = analyze_bundle(fpath)
    for typ, name, size, data in results:
        print(f"  Type: {typ}, Name/Info: {name}, Size: {size}")
        if data and isinstance(data, bytes):
            try:
                text = data.decode('utf-8', errors='replace')
                print(f"  Text preview: {text[:300]}")
            except:
                print(f"  Raw hex: {data[:100].hex()}")
        elif data is None and isinstance(name, str):
            print(f"  TypeTree: {name[:400]}")
