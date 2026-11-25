import test from 'node:test';
import assert from 'node:assert/strict';
import {proposePairs} from '../src/intakePairs.js';

const file = (token, relative_path) => ({token, part: {relative_path}});

test('suggestions bind only one explicit translation within its own incoming folder', () => {
  const a = file('a', 'Дело/Письма/Письмо.pdf');
  const b = file('b', 'Дело/Письма/Письмо-перевод.pdf');
  const other = file('c', 'Дело/Май/Письмо.pdf');
  const [pair] = proposePairs([a, b, other]);
  assert.equal(pair.original, a);
  assert.equal(pair.translation, b);
  assert.equal(proposePairs([a, b, other, file('d', 'Дело/Письма/Письмо ORIGINAL.pdf')]).length, 0);
  assert.equal(proposePairs([a, file('d', 'Дело/Письма/Письмо en.pdf')]).length, 0);
});

test('parallel original and translation folders require exact basenames', () => {
  const a = file('a', 'Поставка/Оригиналы/Договор.pdf');
  const b = file('b', 'Поставка/Переводы/Договор.pdf');
  assert.equal(proposePairs([a, b]).length, 1);
  assert.equal(proposePairs([a, file('c', 'Поставка/Переводы/Другой.pdf')]).length, 0);
  assert.equal(proposePairs([a, file('c', 'Поставка/Переводы/Договор-оригинал.pdf')]).length, 0);
});
