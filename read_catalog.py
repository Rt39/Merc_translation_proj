"""Read the Unity Addressables catalog.bin to find bundle locations."""
import sys, io, struct, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import UnityPy

catalog_path = r"E:\SteamLibrary\steamapps\common\メルクストーリア - 癒術士と心の旋律 -\メルストM_Data\StreamingAssets\aa\catalog.bin"

# catalog.bin could be a Unity SerializedFile or a raw JSON
with open(catalog_path, 'rb') as f:
    header = f.read(32)
    print(f"Header bytes: {header[:16].hex()}")
    print(f"Header ascii: {header[:16].decode('ascii', errors='replace')}")

# Try loading as UnityFS bundle
try:
    env = UnityPy.load(catalog_path)
    print(f"\nUnityPy objects: {len(list(env.objects))}")
    for obj in env.objects:
        print(f"  Type: {obj.type.name}, Size: {obj.byte_size}")
        if obj.type.name == "TextAsset":
            data = obj.read()
            text = data.m_Script
            if isinstance(text, bytes):
                text = text.decode('utf-8', errors='replace')
            print(f"  TextAsset name: {data.m_Name}")
            print(f"  Content length: {len(text)}")
            # Try parsing as JSON
            try:
                cat = json.loads(text)
                print(f"  JSON keys: {list(cat.keys())[:20]}")
                # Look for InternalIds which contain paths
                if 'm_InternalIds' in cat:
                    ids = cat['m_InternalIds']
                    print(f"  InternalIds count: {len(ids)}")
                    # Find story-related entries
                    story_ids = [i for i in ids if 'Story' in str(i) or 'story' in str(i)]
                    print(f"  Story-related IDs: {len(story_ids)}")
                    for sid in story_ids[:10]:
                        print(f"    {sid}")
                    # Find our specific bundle
                    target_ids = [i for i in ids if 'eb777f2829400cfced05a3761d77fd6a' in str(i)]
                    print(f"\n  Target bundle entries:")
                    for tid in target_ids:
                        print(f"    {tid}")
                    # Show some sample paths to understand URL patterns
                    bundle_ids = [i for i in ids if '.bundle' in str(i)]
                    print(f"\n  Sample bundle paths ({len(bundle_ids)} total):")
                    for bid in bundle_ids[:15]:
                        print(f"    {bid}")
                    # Look for ExternalResource or cache-related
                    ext_ids = [i for i in ids if 'External' in str(i) or 'Cache' in str(i) or 'cache' in str(i)]
                    if ext_ids:
                        print(f"\n  External/Cache related:")
                        for eid in ext_ids:
                            print(f"    {eid}")
                if 'm_ProviderIds' in cat:
                    print(f"\n  Providers: {cat['m_ProviderIds']}")
                if 'm_ResourceProviderData' in cat:
                    print(f"\n  ResourceProviderData:")
                    for rpd in cat['m_ResourceProviderData']:
                        print(f"    {rpd}")
            except json.JSONDecodeError:
                print(f"  Not JSON, first 500 chars: {text[:500]}")
except Exception as e:
    print(f"UnityPy load failed: {e}")

# Try reading raw
with open(catalog_path, 'rb') as f:
    raw = f.read()
print(f"\nRaw file size: {len(raw)}")

# Search for story bundle hash in the raw data
target = b"eb777f2829400cfced05a3761d77fd6a"
idx = raw.find(target)
if idx >= 0:
    ctx = raw[max(0,idx-200):idx+200]
    print(f"\nFound target at offset {idx}:")
    print(f"  Context: {ctx.decode('utf-8', errors='replace')[:400]}")
else:
    print("\nTarget bundle hash not found in catalog")

# Search for path patterns
for pattern in [b"StoryMasterData", b"AssetBundle/", b"persistentDataPath", b"ExternalResource"]:
    idx = raw.find(pattern)
    if idx >= 0:
        ctx = raw[max(0,idx-50):idx+100]
        print(f"\n'{pattern.decode()}' at offset {idx}:")
        print(f"  {ctx.decode('utf-8', errors='replace')[:200]}")
