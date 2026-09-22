import fs from 'node:fs';
import { computeAssetId, verifyAssetId, SCHEMA_VERSION } from '@evomap/gep-sdk';

// Deliberately a small, stdout-only bridge.  It never contacts EvoMap Hub and
// accepts one JSON request on stdin so callers can keep credentials out of it.
const input = await new Promise((resolve, reject) => {
  let data = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => { data += chunk; });
  process.stdin.on('end', () => resolve(data));
  process.stdin.on('error', reject);
});

try {
  const request = JSON.parse(input || '{}');
  const assets = Array.isArray(request.assets) ? request.assets : [];
  const result = assets.map(asset => {
    const withId = { ...asset, asset_id: computeAssetId(asset) };
    return { asset: withId, asset_id: withId.asset_id, verified: verifyAssetId(withId) };
  });
  process.stdout.write(JSON.stringify({ schema_version: SCHEMA_VERSION, assets: result }));
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String(error?.message || error) }));
  process.exitCode = 1;
}
