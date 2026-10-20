// dockhand-export-secrets.cjs against a fake Dockhand database, its values made up here and
// encrypted as Dockhand's encrypt() does. Run by test_dockhand_export_secrets.py.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { DatabaseSync } = require('node:sqlite');

const script = fs.readFileSync(path.join(__dirname, '..', 'dockhand-export-secrets.cjs'));
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dockhand-export-'));
fs.mkdirSync(path.join(dir, 'db'));
const dbFile = path.join(dir, 'db', 'dockhand.db');
const keyFile = path.join(dir, '.encryption_key');
const key = crypto.randomBytes(32);
fs.writeFileSync(keyFile, key);

// Dockhand's encrypt(): enc:v1:<base64(iv + authTag + ciphertext)>
function encrypt(plain) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
  return 'enc:v1:' + Buffer.concat([iv, cipher.getAuthTag(), ciphertext]).toString('base64');
}

const db = new DatabaseSync(dbFile);
db.exec(`
  CREATE TABLE environments (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, host TEXT);
  CREATE TABLE stack_environment_variables (id INTEGER PRIMARY KEY AUTOINCREMENT, stack_name TEXT NOT NULL,
    environment_id INTEGER REFERENCES environments(id), key TEXT NOT NULL, value TEXT NOT NULL,
    is_secret INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
  INSERT INTO environments (id, name) VALUES (1, 'docker-vm'), (2, 'nas');`);
const insert = db.prepare('INSERT INTO stack_environment_variables (stack_name, environment_id, key, value, is_secret) VALUES (?, ?, ?, ?, ?)');
const multiLine = 'line one' + String.fromCharCode(10) + 'line two, ünïcödé "quoted" $dollar';
insert.run('kea', 1, 'KEA_API_PASSWORD', encrypt('fake-pass-1'), 1);
insert.run('kea', 1, 'KEA_SUBNET', 'not a secret', 0);
insert.run('netbox', 1, 'MULTI', encrypt(multiLine), 1);
insert.run('netbox', 1, 'EMPTY', '', 1);
insert.run('netbox', 1, 'LEGACY_PLAIN', 'stored before encryption', 1);
insert.run('orphan', null, 'NO_ENV', encrypt('fake-orphan'), 1);
insert.run('garage', 2, 'BROKEN', 'enc:v1:' + crypto.randomBytes(40).toString('base64'), 1);
db.close();

const expected = JSON.stringify([
  { environment: null, stack: 'orphan', key: 'NO_ENV', value: 'fake-orphan' },
  { environment: 'docker-vm', stack: 'kea', key: 'KEA_API_PASSWORD', value: 'fake-pass-1' },
  { environment: 'docker-vm', stack: 'netbox', key: 'EMPTY', value: '' },
  { environment: 'docker-vm', stack: 'netbox', key: 'LEGACY_PLAIN', value: 'stored before encryption' },
  { environment: 'docker-vm', stack: 'netbox', key: 'MULTI', value: multiLine },
]);
const hash = () => crypto.createHash('sha256').update(fs.readFileSync(dbFile)).digest('hex');
const before = hash();
let failures = 0;
function check(name, ok) { console.log((ok ? 'ok     ' : 'not ok ') + name); if (!ok) failures++; }
function run(env, dataDir = dir) {
  return spawnSync(process.execPath, ['--no-warnings', '-'],
    { input: script, env: { PATH: process.env.PATH, DATA_DIR: dataDir, ...env }, encoding: 'utf8' });
}
const output = (r) => { try { return JSON.stringify(JSON.parse(r.stdout)); } catch { return null; } };

let r = run({});
check('key file: exit code 2, as one row cannot be decrypted', r.status === 2);
check('key file: exactly the five readable secrets, the non-secret left out', output(r) === expected);
check('key file: the count names the broken row', r.stderr.trim() === '5 secret variables from 3 stacks exported; 1 could not be decrypted: nas / garage / BROKEN');

fs.renameSync(keyFile, path.join(dir, 'moved_key'));
r = run({ ENCRYPTION_KEY: key.toString('base64') });
check('ENCRYPTION_KEY with no key file: the same output', r.status === 2 && output(r) === expected);
r = run({ ENCRYPTION_KEY: 'too-short' });
check('an invalid ENCRYPTION_KEY and no key file: stops, nothing exported', r.status === 1 && r.stdout === '' && r.stderr.startsWith('STOP: no valid ENCRYPTION_KEY'));
fs.writeFileSync(keyFile, crypto.randomBytes(32));
r = run({});
check('the wrong key: every encrypted row named, the plain ones still read', r.status === 2 && JSON.parse(r.stdout).length === 2 && r.stderr.includes('4 could not be decrypted'));
fs.writeFileSync(keyFile, Buffer.alloc(16));
r = run({});
check('a 16-byte key file: stops', r.status === 1 && r.stdout === '' && r.stderr.includes('holds 16 bytes'));
r = run({ DATABASE_URL: 'postgres://example' });
check('a PostgreSQL Dockhand: stops', r.status === 1 && r.stdout === '' && r.stderr.includes('PostgreSQL'));
r = run({}, path.join(dir, 'nowhere'));
check('no database: stops', r.status === 1 && r.stdout === '' && r.stderr.includes('no database at'));
check('the database unchanged by every run', hash() === before);

fs.rmSync(dir, { recursive: true, force: true });
console.log(failures ? failures + ' did not pass' : 'ALL PASSED');
process.exitCode = failures ? 1 : 0;
