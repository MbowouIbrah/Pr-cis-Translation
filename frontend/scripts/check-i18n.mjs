/**
 * Vérifie les traductions — le seul garde-fou automatique de l'interface.
 *
 * TROIS DÉFAUTS, TOUS INVISIBLES À LA COMPILATION
 * ----------------------------------------------
 * `tsc` ne sait rien de i18next : `t('library.inexistante')` compile, et
 * affiche la CLÉ BRUTE à l'écran. Trois choses se contrôlent ici :
 *
 *   1. toute clé employée dans le code existe en français ;
 *   2. les deux dictionnaires portent EXACTEMENT les mêmes clés — une clé
 *      présente d'un seul côté s'affiche en brut dans l'autre langue ;
 *   3. aucune traduction n'est vide.
 *
 * Les clés construites dynamiquement (`t(\`plans.${plan}\`)`) ne sont pas
 * vérifiables statiquement : elles sont listées à part, pour être relues.
 *
 *     npm run check:i18n
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ICI = dirname(fileURLToPath(import.meta.url));
const SRC = join(ICI, '..', 'src');
const LOCALES = join(SRC, 'locales');

/** Toutes les feuilles d'un dictionnaire, en notation pointée. */
function aplatir(obj, prefixe = '') {
  const out = new Map();
  for (const [k, v] of Object.entries(obj)) {
    if (v && typeof v === 'object') {
      for (const [kk, vv] of aplatir(v, `${prefixe}${k}.`)) out.set(kk, vv);
    } else {
      out.set(prefixe + k, v);
    }
  }
  return out;
}

function fichiersSource(dir) {
  const out = [];
  for (const nom of readdirSync(dir)) {
    const p = join(dir, nom);
    if (statSync(p).isDirectory()) {
      if (nom !== 'locales') out.push(...fichiersSource(p));
    } else if (/\.(tsx?|jsx?)$/.test(nom) && !nom.endsWith('.d.ts')) {
      out.push(p);
    }
  }
  return out;
}

const langues = readdirSync(LOCALES);
const dicos = new Map(
  langues.map((lg) => [
    lg,
    aplatir(JSON.parse(readFileSync(join(LOCALES, lg, 'translation.json'), 'utf8'))),
  ]),
);

const erreurs = [];
const avertissements = [];

// La référence est le FRANÇAIS, langue dans laquelle l'interface est écrite —
// pas « la première du dossier ». L'ordre de `readdirSync` est alphabétique :
// la référence était donc l'anglais, et le jour où une langue commençant par
// « a » ou « d » arrive, elle changeait toute seule.
const REFERENCE = 'fr';
const reference = langues.includes(REFERENCE) ? REFERENCE : langues[0];
const autres = langues.filter((lg) => lg !== reference);

// ── 1. Les dictionnaires concordent, et rien n'est vide ────────────────────
for (const lg of autres) {
  for (const cle of dicos.get(reference).keys()) {
    if (!dicos.get(lg).has(cle)) erreurs.push(`manque en « ${lg} » : ${cle}`);
  }
  for (const cle of dicos.get(lg).keys()) {
    if (!dicos.get(reference).has(cle)) erreurs.push(`manque en « ${reference} » : ${cle}`);
  }
}
for (const lg of langues) {
  for (const [cle, valeur] of dicos.get(lg)) {
    if (typeof valeur !== 'string' || valeur.trim() === '') {
      erreurs.push(`traduction vide — ${lg}.${cle}`);
    }
  }
}

// ── 2. Toute clé employée existe ───────────────────────────────────────────
// `t('a.b')` ou `t('a.b', 'defaut')` ou `t('a.b', { … })`.
const STATIQUE = /\bt\(\s*'([A-Za-z0-9_.]+)'/g;
const DYNAMIQUE = /\bt\(\s*`([^`]*\$\{[^`]*)`/g;

const employees = new Map();   // cle -> [fichiers]
const dynamiques = [];

for (const f of fichiersSource(SRC)) {
  const code = readFileSync(f, 'utf8');
  const court = f.slice(f.indexOf('src'));
  for (const m of code.matchAll(STATIQUE)) {
    if (!employees.has(m[1])) employees.set(m[1], []);
    employees.get(m[1]).push(court);
  }
  for (const m of code.matchAll(DYNAMIQUE)) dynamiques.push(`${court} : t(\`${m[1]}\`)`);
}

// Pluriel i18next : `t('a.b', { count })` lit `a.b_one` / `a.b_other`, sans
// clé `a.b` nue. `_other` est la forme de repli de toute langue : elle suffit.
const PLURIEL = /_(zero|one|two|few|many|other)$/;
const existe = (cle) => dicos.get(reference).has(cle) || dicos.get(reference).has(`${cle}_other`);

for (const [cle, fichiers] of employees) {
  if (!existe(cle)) {
    erreurs.push(`clé absente du dictionnaire : ${cle}   [${fichiers.join(', ')}]`);
  }
}

// ── 3. Clés jamais employées — signalées, pas fatales ──────────────────────
// Une clé peut être atteinte dynamiquement ; on ne supprime rien sur ce seul
// indice. Mais une section entière inutilisée mérite un coup d'œil.
for (const cle of dicos.get(reference).keys()) {
  const employee = employees.has(cle) || employees.has(cle.replace(PLURIEL, ''));
  if (!employee && !dynamiques.length) avertissements.push(`jamais employée : ${cle}`);
}

// ── Rapport ────────────────────────────────────────────────────────────────
console.log(`\nLangues : ${langues.join(', ')}  —  ${dicos.get(reference).size} clés`);
console.log(`Clés employées dans le code : ${employees.size}`);
if (dynamiques.length) {
  console.log(`\nClés construites dynamiquement (à relire à la main) :`);
  for (const d of dynamiques) console.log(`   ${d}`);
}
if (avertissements.length) {
  console.log(`\n${avertissements.length} clé(s) jamais employée(s) :`);
  for (const a of avertissements.slice(0, 20)) console.log(`   ${a}`);
  if (avertissements.length > 20) console.log(`   … et ${avertissements.length - 20} autres`);
}
if (erreurs.length) {
  console.error(`\n${erreurs.length} ERREUR(S) :`);
  for (const e of erreurs) console.error(`   ${e}`);
  process.exit(1);
}
console.log('\nOK — dictionnaires concordants, aucune clé manquante.\n');
