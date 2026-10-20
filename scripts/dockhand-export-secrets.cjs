// Every secret stack variable Dockhand holds, decrypted, as JSON on stdout; a count on stderr.
// Run inside the Dockhand container. Opens the database read-only and writes nothing.
// scripts/dockhand_keyvault_copy.py copies the output into Key Vault (scripts/README.md).
const fs = require('node:fs');
const crypto = require('node:crypto');
const { DatabaseSync } = require('node:sqlite');

const dataDir = process.env.DATA_DIR || '/app/data';
const dbPath = process.env.DOCKHAND_DB || dataDir + '/db/dockhand.db';
const stop = (message) => { console.error('STOP: ' + message + '; nothing exported'); process.exit(1); };

if (process.env.DATABASE_URL) stop('this Dockhand uses PostgreSQL (DATABASE_URL is set), not SQLite');
if (!fs.existsSync(dbPath)) stop('no database at ' + dbPath);

// Dockhand's own order: a valid ENCRYPTION_KEY wins, else the key file in the data folder.
let key = null;
if (process.env.ENCRYPTION_KEY) {
  const fromEnv = Buffer.from(process.env.ENCRYPTION_KEY, 'base64');
  if (fromEnv.length === 32) key = fromEnv;
}
if (!key) {
  const keyPath = dataDir + '/.encryption_key';
  if (!fs.existsSync(keyPath)) stop('no valid ENCRYPTION_KEY and no key file at ' + keyPath);
  key = fs.readFileSync(keyPath);
  if (key.length !== 32) stop('the key file holds ' + key.length + ' bytes, not 32');
}

// enc:v1:<base64(12-byte IV, 16-byte tag, ciphertext)>; a value without the prefix is plain
// text, as Dockhand reads it too.
const PREFIX = 'enc:v1:';
function decrypt(value) {
  if (!value.startsWith(PREFIX)) return value;
  const packed = Buffer.from(value.slice(PREFIX.length), 'base64');
  if (packed.length < 12 + 16 + 1) throw new Error('too short to be ciphertext');
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, packed.subarray(0, 12));
  decipher.setAuthTag(packed.subarray(12, 28));
  return Buffer.concat([decipher.update(packed.subarray(28)), decipher.final()]).toString('utf8');
}

const db = new DatabaseSync(dbPath, { readOnly: true });
const rows = db.prepare(`
  SELECT e.name AS environment, v.stack_name AS stack, v.key AS key, v.value AS value
  FROM stack_environment_variables v LEFT JOIN environments e ON e.id = v.environment_id
  WHERE v.is_secret = 1
  ORDER BY e.name, v.stack_name, v.key`).all();
db.close();

const exported = [];
const failed = [];
for (const row of rows) {
  const where = (row.environment ?? '(no environment)') + ' / ' + row.stack + ' / ' + row.key;
  try {
    exported.push({ environment: row.environment ?? null, stack: row.stack, key: row.key, value: decrypt(row.value ?? '') });
  } catch (error) {
    failed.push(where);
  }
}
process.stdout.write(JSON.stringify(exported, null, 2) + '\n');
const stacks = new Set(exported.map((v) => (v.environment ?? '') + '/' + v.stack));
console.error(exported.length + ' secret variables from ' + stacks.size + ' stacks exported; ' +
  failed.length + ' could not be decrypted' + (failed.length ? ': ' + failed.join(', ') : ''));
if (failed.length) process.exitCode = 2;
