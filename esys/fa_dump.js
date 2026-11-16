// Maintainer tool for the FA / VO Helper tables (ocm/fa_tables.py). Runs with E-Sys's own PsdZ, on the maintainer's
// machine only; users get the finished tables in data\fa\ and never run this.
//   python ocm\esys.py run esys\fa_dump.js <outDir>
// Writes into <outDir>:
//   fafp_codes.tsv   main series, selector, code  - every code the newest FA->FP mapping of each main series knows
//                    (W = option / SALAPA, H = E-word, K = HO-word, T = type key)
//   kis_<GROUP>.tsv  per KIS knowledge base (psdzdata\kiswb\<GROUP>): type keys and their characteristics, option
//                    classes per type key and option availability windows (read-only, through psdzdata's own kis.jar)
//   psdzdata.txt     the psdzdata version the dump was made from
var File = java.io.File, err = java.lang.System.err;
var outDir = new File(arguments[0]);
outDir.mkdirs();
var cfg = Java.type("com.bmw.esys.domain.psdz.PsdzConfiguration").getInstance();
cfg.setPsdzRoot();
var root = new File(cfg.getRoot(), "psdzdata");
var pl = Java.type("com.bmw.esys.domain.psdz.PsdzLogic").getInstance();

function writer(name) {
  return new java.io.PrintWriter(new java.io.OutputStreamWriter(new java.io.FileOutputStream(new File(outDir, name)), "UTF-8"));
}
function attr(el, name) { return el.hasAttribute(name) ? String(el.getAttribute(name)) : ""; }
function byTag(doc, tag) {
  var nl = doc.getElementsByTagNameNS("*", tag);
  return nl.getLength() ? nl : doc.getElementsByTagName(tag);
}

// ---------------------------------------------------------------- psdzdata version
var ver = Java.from(root.list() || []).filter(function (n) { return /\.ver$/i.test(n); });
var pv = writer("psdzdata.txt");
pv.println(ver.length ? String(ver[0]).replace(/\.ver$/i, "") : "unknown");
pv.close();

// ---------------------------------------------------------------- FA->FP mappings: newest per main series
var best = {};
Java.from(new File(root, "swe\\fafp").listFiles() || []).forEach(function (f) {
  var m = String(f.getName()).match(/^fafp_[0-9a-f]{8}\.fap\.(\d{3}_\d{3}_\d{3})$/i);
  if (!m) return;
  try {
    var doc = pl.decryptVerifyCAF(f), hdr = byTag(doc, "sweHeader");
    var ms = hdr.getLength() ? attr(hdr.item(0), "mainSeries") : "";
    if (ms && (!best[ms] || m[1] > best[ms].ver)) best[ms] = { ver: m[1], doc: doc };
  } catch (e) {
    err.println("NOTE fafp " + f.getName() + ": " + e);
  }
});
var fw = writer("fafp_codes.tsv"), nCodes = 0;
fw.println("series\tselector\tcode\tfafp_version");
Object.keys(best).sort().forEach(function (ms) {
  var seen = {}, els = byTag(best[ms].doc, "faElement");
  for (var i = 0; i < els.getLength(); i++) {
    var sel = attr(els.item(i), "selector"), code = attr(els.item(i), "code");
    if (!code || !/^[WHKT]$/.test(sel) || seen[sel + code]) continue;
    seen[sel + code] = 1;
    fw.println(ms + "\t" + sel + "\t" + code + "\t" + best[ms].ver);
    nCodes++;
  }
});
fw.close();
err.println("PROGRESS fafp: " + Object.keys(best).length + " main series, " + nCodes + " codes");

// ---------------------------------------------------------------- KIS knowledge bases (read-only)
var kisJar = new File(root, "extLibs\\kis.jar");
var loader = new java.net.URLClassLoader(Java.to([kisJar.toURI().toURL()], "java.net.URL[]"),
                                         java.lang.ClassLoader.getSystemClassLoader().getParent());
var driver = loader.loadClass("org.hsqldb.jdbc.JDBCDriver").getDeclaredConstructor().newInstance();
function rows(conn, sql, each) {
  var st = conn.createStatement(), rs = st.executeQuery(sql), md = rs.getMetaData(), n = md.getColumnCount(), k = 0;
  while (rs.next()) {
    var r = [];
    for (var i = 1; i <= n; i++) { var v = rs.getString(i); r.push(v === null ? "" : String(v).replace(/[\t\r\n]+/g, " ")); }
    each(r); k++;
  }
  rs.close(); st.close();
  return k;
}
Java.from(new File(root, "kiswb").listFiles() || []).forEach(function (dir) {
  if (!new File(dir, "KIS.script").exists()) return;
  var group = String(dir.getName()), props = new java.util.Properties();
  props.setProperty("user", "SA"); props.setProperty("password", "");
  var url = "jdbc:hsqldb:file:" + new File(dir, "KIS").getPath().replace(/\\/g, "/") + ";readonly=true;ifexists=true;shutdown=true";
  var conn = null;
  try {
    conn = driver.connect(url, props);
    var w = writer("kis_" + group + ".tsv"), counts = [];
    w.println("# KIS " + group);
    counts.push(rows(conn, "SELECT VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH FROM KIS.INFORMATIONWISSENSBASIS",
                     function (r) { w.println("version\t" + r.join(".")); }));
    counts.push(rows(conn, "SELECT NAME, TYPMERKMALE, BESCHREIBUNG FROM KIS.FAHRZEUGTYP",
                     function (r) { w.println("type\t" + r.join("\t")); }));
    counts.push(rows(conn, "SELECT f.NAME, s.SALAPA, s.AST_KLASSE FROM KIS.SALAPA s JOIN KIS.FAHRZEUGTYP f ON f.ID = s.FAHRZEUGTYP_ID",
                     function (r) { w.println("class\t" + r.join("\t")); }));
    counts.push(rows(conn, "SELECT f.NAME, t.SALAPA, t.VBA_VON, t.VBA_BIS FROM KIS.TYPINHALT_VBA t JOIN KIS.FAHRZEUGTYP f ON f.ID = t.FAHRZEUGTYP_ID",
                     function (r) { w.println("window\t" + r.join("\t")); }));
    counts.push(rows(conn, "SELECT EKW_WORT, MAPPING_ART, MAPPING FROM KIS.EKWMAPPING",
                     function (r) { w.println("ekw\t" + r.join("\t")); }));
    w.close();
    err.println("PROGRESS KIS " + group + ": version rows " + counts[0] + ", type keys " + counts[1] + ", class rows " + counts[2]
                + ", windows " + counts[3] + ", E/K-word mappings " + counts[4]);
  } catch (e) {
    err.println("NOTE KIS " + group + ": " + e);
  } finally {
    if (conn) try { conn.close(); } catch (e2) { }   // shutdown=true closes the read-only database with the connection
  }
});
err.println("DONE FA Helper dump in " + outDir);
java.lang.System.exit(0);
