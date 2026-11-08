// Ban rules for the checker page. This is a port of ban_tool.py's filter: keep the two in step.
(function (root) {
  "use strict";

  const THIRDS = ["bottom", "middle", "top"];
  const TRUE_WORDS = ["true", "on", "yes", "enabled"];
  const FALSE_WORDS = ["false", "off", "no", "disabled"];
  const RANDOM_RANGE = /^random-range-(?:(low|middle|high)-)?(-?\d+)-(-?\d+)$/;

  function keyText(key) {
    if (key === true) return "true";
    if (key === false) return "false";
    return String(key);
  }

  // Archipelago's random keywords. Some games have real values like random_all, so match exactly.
  function isRandomWord(value) {
    return value === "random" || value.startsWith("random-");
  }

  function parseInteger(text) {
    return /^\s*[+-]?\d+\s*$/.test(text) ? parseInt(text, 10) : null;
  }

  function isDict(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  // Python truthiness, which decides whether a list/dict/text setting was filled in.
  function isFilled(value) {
    if (Array.isArray(value)) return value.length > 0;
    if (isDict(value)) return Object.keys(value).length > 0;
    return Boolean(value);
  }

  function bucketsOf(option) {
    if (option.kind === "choice") return option.values.map((v) => String(v).toLowerCase());
    if (option.kind === "range") {
      const inside = bucketsBetween(option, option.low, option.high);
      return ["min", "max", ...THIRDS].filter((b) => inside.has(b));
    }
    return [];
  }

  // Which third a value falls in, counting only the values between min and max.
  function thirdOf(option, value) {
    return Math.floor(((value - option.low - 1) * 3) / (option.high - option.low - 1));
  }

  function bucketsBetween(option, a, b) {
    const buckets = new Set();
    if (a <= option.low && option.low <= b) buckets.add("min");
    if (a <= option.high && option.high <= b) buckets.add("max");
    const first = Math.max(a, option.low + 1);
    const last = Math.min(b, option.high - 1);
    if (first <= last) {
      THIRDS.slice(thirdOf(option, first), thirdOf(option, last) + 1).forEach((t) => buckets.add(t));
    }
    return buckets;
  }

  function selectionOf(option, value) {
    let original;
    if (value === null || value === undefined) {
      value = option.default;
      original = `${value} (default)`;
    } else if (isDict(value)) {
      original = Object.entries(value).filter(([, w]) => w).map(([k, w]) => `${keyText(k)}: ${w}`).join(", ");
    } else if (Array.isArray(value)) {
      return selection({ original: JSON.stringify(value), warning: `${option.name} is a list, which this setting doesn't take` });
    } else {
      original = keyText(value);
    }

    const entries = isDict(value) ? Object.entries(value) : [[keyText(value), 1]];
    const weights = new Map();
    for (const [k, w] of entries) if (w) weights.set(keyText(k).toLowerCase(), w);
    if (weights.size === 0) {
      return selection({ buckets: bucketsOf(option), original, warning: `${option.name} has no non-zero weight` });
    }
    const result = option.kind === "choice" ? resolveChoice(option, weights) : resolveRange(option, weights);
    result.original = original;
    return result;
  }

  function selection(fields) {
    return Object.assign({ isRandom: false, buckets: new Set(), original: "", warning: "" }, fields, {
      buckets: new Set(fields.buckets || []),
    });
  }

  function resolveChoice(option, weights) {
    const values = new Set(option.values.map((v) => String(v).toLowerCase()));
    const keys = [...weights.keys()];
    if (keys.length === 1 && keys[0] === "random") return selection({ isRandom: true });
    if (keys.some((k) => isRandomWord(k) && !values.has(k))) {
      return selection({ buckets: values, warning: `${option.name} is a weighted random, so it uses every value` });
    }
    const resolved = new Set(keys.map((k) => canonicalChoice(k, values)));
    const sameSet = resolved.size === values.size && [...values].every((v) => resolved.has(v));
    if (sameSet && new Set(weights.values()).size === 1) return selection({ isRandom: true });
    const unknown = [...resolved].filter((v) => !values.has(v)).sort();
    const warning = unknown.length ? `${option.name} has a value the template doesn't list: ${unknown.join(", ")}` : "";
    return selection({ buckets: resolved, warning });
  }

  function canonicalChoice(value, values) {
    if (values.has(value)) return value;
    for (const words of [TRUE_WORDS, FALSE_WORDS]) {
      if (words.includes(value)) return words.find((w) => values.has(w)) || value;
    }
    return value;
  }

  function resolveRange(option, weights) {
    const keys = [...weights.keys()];
    if (keys.length === 1 && keys[0] === "random") return selection({ isRandom: true });
    const buckets = new Set();
    let warning = "";
    for (const key of keys) {
      const match = key.match(RANDOM_RANGE);
      if (match) {
        const x = parseInt(match[2], 10), y = parseInt(match[3], 10);
        const a = Math.min(x, y), b = Math.max(x, y);
        if (keys.length === 1 && !match[1] && a <= option.low && b >= option.high) return selection({ isRandom: true });
        bucketsBetween(option, a, b).forEach((t) => buckets.add(t));
        warning = `${option.name} is a weighted or narrowed random, so it uses every value it can land on`;
      } else if (isRandomWord(key)) {
        bucketsOf(option).forEach((t) => buckets.add(t));
        warning = `${option.name} is a weighted random, so it uses every value`;
      } else {
        const number = key in option.aliases ? option.aliases[key] : parseInteger(key);
        if (number === null || number < option.low || number > option.high) {
          warning = `${option.name} value ${key} is not valid for ${option.low}-${option.high}`;
        } else {
          bucketsBetween(option, number, number).forEach((t) => buckets.add(t));
        }
      }
    }
    return selection({ buckets, warning });
  }

  // Returns the replacement for progression_balancing, or null if it's within the cap.
  function capProgression(settings, data) {
    const value = settings.progression_balancing;
    if (value === null || value === undefined) return null;
    const entries = isDict(value) ? Object.entries(value) : [[keyText(value), 1]];
    let hasRandom = false, overCap = false;
    for (const [k, w] of entries) {
      if (!w) continue;
      const key = keyText(k).toLowerCase();
      const match = key.match(RANDOM_RANGE);
      if (match) {
        hasRandom = true;
        overCap = overCap || Math.max(parseInt(match[2], 10), parseInt(match[3], 10)) > data.progression_cap;
      } else if (isRandomWord(key)) {
        hasRandom = overCap = true;
      } else {
        const number = key in data.progression_aliases ? data.progression_aliases[key] : parseInteger(key);
        overCap = overCap || (number !== null && number > data.progression_cap);
      }
    }
    if (!overCap) return null;
    return hasRandom ? `random-range-0-${data.progression_cap}` : String(data.progression_cap);
  }

  function counted(game) {
    return Object.entries(game.options || {})
      .map(([name, option]) => Object.assign({ name }, option))
      .filter((o) => o.kind === "choice" || o.kind === "range");
  }

  // A value marked fine counts as used up, as in ban_tool.py.
  function gameProgress(game) {
    const options = counted(game);
    const finished = options.filter((o) =>
      bucketsOf(o).every((b) => ["banned", "fine"].includes(stateOf(game, o.name, b)))).length;
    return { finished, counted: options.length };
  }

  function stateOf(game, setting, value) {
    return (game.bans[setting] || {})[value];
  }

  function checkDocument(doc, data) {
    const report = {
      name: null, game: null, error: null, known: false, fullyBanned: false, capped: null,
      forced: [], provisional: [], handEdit: [], alerts: [], warnings: [], specified: 0,
    };
    if (!isDict(doc)) {
      report.error = "This doesn't look like an Archipelago yaml.";
      return report;
    }
    report.name = typeof doc.name === "string" ? doc.name : null;
    if (typeof doc.game !== "string") {
      report.error = "'game' is not a single game name, so the host will check this one by hand.";
      return report;
    }
    const gameName = doc.game;
    report.game = gameName;
    const settings = doc[gameName];
    if (!isDict(settings)) {
      report.error = `There's no settings section for ${gameName}.`;
      return report;
    }
    if (isFilled(doc.triggers) || isFilled(settings.triggers)) {
      report.alerts.push("Uses triggers, which can change settings after this check.");
    }
    report.capped = capProgression(settings, data);

    const game = data.games[gameName];
    if (!game) return report;
    report.known = true;
    if (!game.options) {
      report.error = `The host has no template for ${gameName}, so this page can't check it.`;
      return report;
    }
    const progress = gameProgress(game);
    report.fullyBanned = progress.counted > 0 && progress.finished === progress.counted;

    for (const name of Object.keys(settings)) {
      if (!(name in game.options) && !data.ignored.includes(name) && name !== "triggers") {
        report.warnings.push(`${name} isn't in the host's template, so it wasn't checked.`);
      }
    }

    for (const option of Object.entries(game.options).map(([name, o]) => Object.assign({ name }, o))) {
      if (option.kind !== "choice" && option.kind !== "range") {
        const state = stateOf(game, option.name, option.kind.toLowerCase());
        if (state === "provisional" && isFilled(settings[option.name])) {
          report.provisional.push({ setting: option.name, original: option.kind.toLowerCase(), buckets: [] });
        } else if (state === "banned") {
          report.handEdit.push(option.name);
        }
        continue;
      }
      const result = selectionOf(option, settings[option.name]);
      if (result.isRandom) continue;
      if (result.warning) report.warnings.push(result.warning);
      const buckets = [...result.buckets];
      const banned = buckets.filter((b) => stateOf(game, option.name, b) === "banned").sort();
      if (banned.length) {
        report.forced.push({ setting: option.name, original: result.original, banned, option });
        continue;
      }
      report.specified += 1;
      const pending = buckets.filter((b) => stateOf(game, option.name, b) === "provisional").sort();
      if (pending.length) report.provisional.push({ setting: option.name, original: result.original, buckets: pending, option });
    }
    return report;
  }

  // The lowest and highest number in a range bucket, as [from, to].
  function bucketBounds(option, bucket) {
    if (bucket === "min") return [option.low, option.low];
    if (bucket === "max") return [option.high, option.high];
    const t = THIRDS.indexOf(bucket);
    const n = option.high - option.low - 1;
    return [option.low + 1 + Math.ceil((t * n) / 3), option.low + Math.ceil(((t + 1) * n) / 3)];
  }

  // Human-readable name for a bucket, e.g. "bottom third (1-33)".
  function describeBucket(option, bucket) {
    if (option.kind !== "range") return bucket;
    if (bucket === "min") return `${option.low} (min)`;
    if (bucket === "max") return `${option.high} (max)`;
    const [from, to] = bucketBounds(option, bucket);
    return from === to ? `${bucket} third (${from})` : `${bucket} third (${from}-${to})`;
  }

  function openBuckets(game, option) {
    return bucketsOf(option).filter((b) => !["banned", "fine"].includes(stateOf(game, option.name, b)));
  }

  const api = {
    checkDocument, selectionOf, bucketsOf, bucketsBetween, bucketBounds, capProgression, gameProgress,
    counted, stateOf, describeBucket, openBuckets,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BanChecker = api;
})(this);
