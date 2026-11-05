import assert from 'node:assert/strict';
import { argon2Sync, hkdfSync } from 'node:crypto';
import { test } from 'node:test';

import { decryptWithKeyPair, deriveKeysFromPassword, encryptForPublicKey, generateKdfSalt, generateKeyPair, generateRecoveryKey, importRecoveryKey, rewrapPrivateKey, unwrapKeyPair } from '../../src/gradeflow_app/static/gradeflow/account-crypto.js';
import { Aes128Gcm, CipherSuite, DhkemX25519HkdfSha256, HkdfSha256 } from '../../src/gradeflow_app/static/vendor/hpke-core-1.9.0/hpke.min.js';

const KDF_SALT = Buffer.alloc(16, 1).toString('base64');

test('the authentication key matches an independent Argon2id and HKDF', async () => {
  const secret = argon2Sync('argon2id', { message: 'correct horse', nonce: Buffer.from(KDF_SALT, 'base64'), parallelism: 1, tagLength: 32, memory: 64 * 1024, passes: 3 });
  const expected = Buffer.from(hkdfSync('sha256', secret, Buffer.alloc(0), 'gradeflow:auth', 32)).toString('hex');
  const { authKey, wrappingKey } = await deriveKeysFromPassword('correct horse', KDF_SALT);
  assert.equal(authKey, expected);
  assert.equal(wrappingKey.extractable, false);
  assert.deepEqual(wrappingKey.usages.toSorted(), ['unwrapKey', 'wrapKey']);
  assert.notEqual((await deriveKeysFromPassword('correct horse', generateKdfSalt())).authKey, authKey);
});

test('the private key unwraps only with its wrapping key, and can be rewrapped with another', async () => {
  const { wrappingKey } = await deriveKeysFromPassword('first password', KDF_SALT);
  const { wrappingKey: newWrappingKey } = await deriveKeysFromPassword('second password', KDF_SALT);
  const keys = await generateKeyPair(wrappingKey);
  assert.equal(Uint8Array.fromBase64(keys.publicKey).length, 32);
  assert.equal(Uint8Array.fromBase64(keys.wrappedPrivateKey).length, 76);

  const keyPair = await unwrapKeyPair(keys, wrappingKey);
  assert.equal(keyPair.privateKey.extractable, false);
  await assert.rejects(unwrapKeyPair(keys, newWrappingKey));

  const rewrapped = { ...keys, wrappedPrivateKey: await rewrapPrivateKey(keys.wrappedPrivateKey, wrappingKey, newWrappingKey) };
  const encrypted = await encryptForPublicKey(keys.publicKey, new Uint8Array([9]), 'context');
  assert.deepEqual(await decryptWithKeyPair(await unwrapKeyPair(rewrapped, newWrappingKey), encrypted, 'context'), new Uint8Array([9]));
  await assert.rejects(rewrapPrivateKey(keys.wrappedPrivateKey, newWrappingKey, wrappingKey));
});

test('a recovery key unwraps a copy of the private key, and a wrong one does not', async () => {
  const recoveryKey = generateRecoveryKey();
  assert.match(recoveryKey, /^[0-9a-f]{4}(-[0-9a-f]{4}){7}$/);
  const { wrappingKey } = await deriveKeysFromPassword('a password', KDF_SALT);
  const { wrappingKey: newWrappingKey } = await deriveKeysFromPassword('a new password', KDF_SALT);
  const keys = await generateKeyPair(wrappingKey);
  const recoveryWrappedPrivateKey = await rewrapPrivateKey(keys.wrappedPrivateKey, wrappingKey, await importRecoveryKey(recoveryKey));

  const typed = ` ${recoveryKey.toUpperCase().replaceAll('-', ' ')} `;
  const wrappedPrivateKey = await rewrapPrivateKey(recoveryWrappedPrivateKey, await importRecoveryKey(typed), newWrappingKey);
  const encrypted = await encryptForPublicKey(keys.publicKey, new Uint8Array([7]), 'context');
  assert.deepEqual(await decryptWithKeyPair(await unwrapKeyPair({ ...keys, wrappedPrivateKey }, newWrappingKey), encrypted, 'context'), new Uint8Array([7]));

  await assert.rejects(rewrapPrivateKey(recoveryWrappedPrivateKey, await importRecoveryKey(generateRecoveryKey()), newWrappingKey));
  await assert.rejects(importRecoveryKey('not-a-key'));
  await assert.rejects(importRecoveryKey('abcd'));
});

test('data encrypted for a public key decrypts only with its key pair and the same context', async () => {
  const { wrappingKey } = await deriveKeysFromPassword('a password', KDF_SALT);
  const keys = await generateKeyPair(wrappingKey);
  const otherKeys = await generateKeyPair(wrappingKey);
  const courseKey = crypto.getRandomValues(new Uint8Array(32));
  const encrypted = await encryptForPublicKey(keys.publicKey, courseKey, 'gradeflow:course-key:1:2');
  assert.equal(Uint8Array.fromBase64(encrypted).length, 80);
  assert.deepEqual(await decryptWithKeyPair(await unwrapKeyPair(keys, wrappingKey), encrypted, 'gradeflow:course-key:1:2'), courseKey);
  await assert.rejects(decryptWithKeyPair(await unwrapKeyPair(keys, wrappingKey), encrypted, 'gradeflow:course-key:1:3'));
  await assert.rejects(decryptWithKeyPair(await unwrapKeyPair(otherKeys, wrappingKey), encrypted, 'gradeflow:course-key:1:2'));
});

test('the vendored HPKE decrypts RFC 9180 test vector A.1', async () => {
  const suite = new CipherSuite({ kem: new DhkemX25519HkdfSha256(), kdf: new HkdfSha256(), aead: new Aes128Gcm() });
  const pkcs8 = Uint8Array.fromHex('302e020100300506032b656e04220420' + '4612c550263fc8ad58375df3f557aac531d26850903e55a9f23f21d8534e8ac8');
  const recipientKey = {
    privateKey: await crypto.subtle.importKey('pkcs8', pkcs8, 'X25519', false, ['deriveBits']),
    publicKey: await suite.kem.deserializePublicKey(Uint8Array.fromHex('3948cfe0ad1ddb695d780e59077195da6c56506b027329794ab02bca80815c4d')),
  };
  const plaintext = await suite.open(
    {
      recipientKey,
      enc: Uint8Array.fromHex('37fda3567bdbd628e88668c3c8d7e97d1d1253b6d4ea6d44c150f741f1bf4431'),
      info: Uint8Array.fromHex('4f6465206f6e2061204772656369616e2055726e'),
    },
    Uint8Array.fromHex('f938558b5d72f1a23810b4be2ab4f84331acc02fc97babc53a52ae8218a355a96d8770ac83d07bea87e13c512a'),
    Uint8Array.fromHex('436f756e742d30'),
  );
  assert.equal(new TextDecoder().decode(plaintext), 'Beauty is truth, truth beauty');
});
