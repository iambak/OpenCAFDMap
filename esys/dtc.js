// DTC (OpenLauncher section in E-Sys): read the car's fault memory and clear it after coding, the way
// BimmerCode does. Talks to the car's gateway directly over ENET (HSFZ, TCP 6801) next to E-Sys's own connection:
//   - modules: one functional request (22 F186 to 0xDF, as E-Sys sends it) and every module that answers;
//   - read:    19 02 0C (report DTCs by status mask: pending + confirmed) per module;
//   - clear:   one functional 14 FF FF FF (clear all) to 0xDF, as BimmerCode sends after coding; a module that doesn't
//              answer that gets it directly. Optional: BMW's info memory (31 01 0F 06) per module.
// Fault texts come from Open CAFD Map's shipped table (data\dtc\, built from ISTA by ocm\dtc_tables.py), by
// diagnostic address + fault code. Loaded by tools.js. Nothing here touches coding data, the FA or the SVT.
(function (h) {
  var System = java.lang.System, File = java.io.File, Sw = javax.swing, Awt = java.awt;
  var log = h.log, cls = h.cls;
  var TESTER = 0xF4, FUNCTIONAL = 0xDF;
  var RED = new Awt.Color(0xB4, 0x23, 0x18), ORANGE = new Awt.Color(0xB5, 0x5A, 0x00), GREEN = new Awt.Color(0x0B, 0x7A, 0x3E),
      GREY = new Awt.Color(0x80, 0x80, 0x88);

  // ------------------------------------------------------------------ fault texts (data\dtc\faults.tsv.gz)
  var T = null;
  function texts() {
    if (T) return T;
    var t = {}, f = new File(new File(h.dataDir, "dtc"), "faults.tsv.gz");
    if (f.isFile()) {
      var r = new java.io.BufferedReader(new java.io.InputStreamReader(
        new java.util.zip.GZIPInputStream(new java.io.FileInputStream(f), 65536), "UTF-8"), 65536), line;
      r.readLine();
      while ((line = r.readLine()) !== null) {
        var s = String(line), a = s.indexOf("\t");
        t[s.substring(0, a + 7)] = s.substring(a + 8);      // "29\t480692" -> "en|de\ten|de..."
      }
      r.close();
    }
    T = t;
    log("DTC: " + Object.keys(t).length + " fault texts loaded");
    return T;
  }
  function german() { return String(java.util.Locale.getDefault().getLanguage()) === "de"; }
  // module title by diagnostic address (data\dtc\modules.tsv.gz), for modules E-Sys's SVT doesn't name
  var M = null;
  function moduleTitle(addr) {
    if (!M) {
      M = {};
      var f = new File(new File(h.dataDir, "dtc"), "modules.tsv.gz");
      if (f.isFile()) {
        var r = new java.io.BufferedReader(new java.io.InputStreamReader(
          new java.util.zip.GZIPInputStream(new java.io.FileInputStream(f)), "UTF-8")), line;
        r.readLine();
        while ((line = r.readLine()) !== null) {
          var s = String(line).split("\t");
          M[parseInt(s[0], 16)] = s[1];
        }
        r.close();
      }
    }
    var v = M[addr];
    if (!v) return "";
    var en = v.split("|")[0], de = v.split("|")[1] || "";
    return german() ? de || en : en || de;
  }
  // {text, others[]} for a module address and a 6-hex-digit fault code, in E-Sys's language
  function textFor(addr, code) {
    var v = texts()[hex2(addr) + "\t" + code];
    if (!v) return { text: "", others: [] };
    var all = v.split("\t").map(function (p) {
      var en = p.split("|")[0], de = p.split("|")[1] || "";
      return (german() ? de || en : en || de);
    });
    return { text: all[0], others: all.slice(1) };
  }

  function hex2(n) { var s = (n & 0xFF).toString(16).toUpperCase(); return s.length < 2 ? "0" + s : s; }
  function hexOf(bytes, from) {
    var out = "";
    for (var i = from || 0; i < bytes.length; i++) out += hex2(bytes[i]);
    return out;
  }
  function bytesOf(hex) {
    var b = Java.type("byte[]"), out = new b(hex.length / 2);
    for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16) << 24 >> 24;
    return out;
  }

  // ------------------------------------------------------------------ HSFZ (BMW ENET diagnostics over TCP)
  // Frame: 4-byte length of what follows the control word, 2-byte control word (0x0001 diagnostic message,
  // 0x0002 acknowledge, 0x0012 alive check), then source, target and the UDS bytes.
  function parseUrl(url) {
    var m = /^(?:tcp:\/\/)?([^:\/\s]+)(?::(\d+))?/.exec(String(url || "").trim());
    return m ? { host: m[1], port: m[2] ? parseInt(m[2], 10) : 6801 } : null;
  }

  function Session(url) {
    var a = parseUrl(url);
    if (!a) throw "no gateway address";
    var sock = new java.net.Socket();
    sock.connect(new java.net.InetSocketAddress(a.host, a.port), 4000);
    sock.setTcpNoDelay(true);
    var input = new java.io.DataInputStream(new java.io.BufferedInputStream(sock.getInputStream()));
    var out = new java.io.DataOutputStream(sock.getOutputStream());
    this.where = a.host + ":" + a.port;

    function send(ctrl, body) {
      out.writeInt(body.length); out.writeShort(ctrl); out.write(body); out.flush();
    }
    function sendDiag(target, uds) {
      var b = bytesOf(hex2(TESTER) + hex2(target) + uds);
      send(0x0001, b);
    }
    // next frame, or null when nothing arrives within ms
    function next(ms) {
      sock.setSoTimeout(Math.max(1, ms));
      try {
        var len = input.readInt(), ctrl = input.readUnsignedShort();
        var b = Java.type("byte[]"), body = new b(len);
        input.readFully(body);
        if (ctrl === 0x0012) { send(0x0013, bytesOf("00" + hex2(TESTER))); return next(ms); }   // alive check
        return { ctrl: ctrl, body: body };
      } catch (e) {
        if (e instanceof java.net.SocketTimeoutException) return null;
        throw e;
      }
    }
    // collect answers to one request: {addr: "hex response"}; a functional request waits until the car goes quiet
    this.ask = function (target, uds, opts) {
      opts = opts || {};
      var maxMs = opts.maxMs || 3000, quietMs = opts.quietMs || 400, functional = target === FUNCTIONAL;
      var got = {}, n = 0, pending = {}, nPending = 0, now = System.currentTimeMillis();
      var deadline = now + maxMs, last = now;
      sendDiag(target, uds);
      while (true) {
        now = System.currentTimeMillis();
        if (now >= deadline) break;                                      // nothing (more) within the time allowed
        var wait = deadline - now;
        if (functional && n && !nPending) {                              // answers came: stop once the car goes quiet
          if (now - last >= quietMs) break;
          wait = Math.min(wait, quietMs - (now - last));
        }
        var f = next(wait);
        if (!f) continue;
        if (f.ctrl === 0x0002) continue;                                 // the gateway's acknowledge
        if (f.ctrl >= 0x0040 && f.ctrl <= 0x0045) throw "gateway refused the request (HSFZ 0x" + f.ctrl.toString(16) + ")";
        if (f.ctrl !== 0x0001 || f.body.length < 3 || (f.body[1] & 0xFF) !== TESTER) continue;
        var src = f.body[0] & 0xFF, resp = hexOf(f.body, 2);
        if (!functional && src !== target) continue;
        last = System.currentTimeMillis();
        if (resp.length === 6 && resp.substring(0, 2) === "7F" && resp.substring(4) === "78") {   // response pending
          if (!pending[src]) { pending[src] = true; nPending++; }
          deadline = last + maxMs;
          continue;
        }
        if (pending[src]) { delete pending[src]; nPending--; }
        if (got[src] === undefined) n++;
        got[src] = resp;
        if (!functional) break;
      }
      return got;
    };
    this.close = function () { try { sock.close(); } catch (e) {} };
  }

  // ------------------------------------------------------------------ fault memory
  function discover(s) {
    var got = s.ask(FUNCTIONAL, "22F186", { maxMs: 4000, quietMs: 700 });
    return Object.keys(got).map(Number).sort(function (x, y) { return x - y; });
  }

  // DTC status byte (ISO 14229): bit 0 test failed (present now), bit 2 pending, bit 3 confirmed (stored)
  function statusText(st) {
    if (st & 0x01) return "present";
    if (st & 0x08) return "stored";
    if (st & 0x04) return "pending";
    return "status " + hex2(st);
  }

  function readOne(s, addr) {
    var r = s.ask(addr, "19020C", { maxMs: 4000 })[addr];
    if (!r) return { addr: addr, error: "no answer" };
    if (r.substring(0, 2) === "7F") return { addr: addr, error: "refused (" + r + ")" };
    if (r.substring(0, 4) !== "5902") return { addr: addr, error: "unexpected answer " + r.substring(0, 16) };
    var faults = [];
    for (var i = 6; i + 8 <= r.length; i += 8) {
      var code = r.substring(i, i + 6), st = parseInt(r.substring(i + 6, i + 8), 16), t = textFor(addr, code);
      faults.push({ code: code, status: st, state: statusText(st), text: t.text, others: t.others });
    }
    return { addr: addr, faults: faults };
  }

  function readAll(s, addrs, progress) {
    return addrs.map(function (a, i) { if (progress) progress(i, addrs.length, a); return readOne(s, a); });
  }

  // clear every module's fault memory: one functional 14 FF FF FF, then directly where no answer came
  function clearAll(s, addrs, infoMemory) {
    var res = {}, got = s.ask(FUNCTIONAL, "14FFFFFF", { maxMs: 6000, quietMs: 800 });
    Object.keys(got).forEach(function (k) { res[k] = got[k]; });
    addrs.forEach(function (a) {
      if (res[a] === undefined) { var r = s.ask(a, "14FFFFFF", { maxMs: 4000 })[a]; res[a] = r || null; }
    });
    var info = {};
    if (infoMemory) addrs.forEach(function (a) { info[a] = s.ask(a, "31010F06", { maxMs: 4000 })[a] || null; });
    var cleared = 0, refused = [], silent = [];
    addrs.forEach(function (a) {
      var r = res[a];
      if (r === "54") cleared++;
      else if (r) refused.push(hex2(a) + " (" + r + ")");
      else silent.push(hex2(a));
    });
    return { cleared: cleared, refused: refused, silent: silent, info: info };
  }

  // ------------------------------------------------------------------ E-Sys: gateway and module names
  function getField(obj, clazz, name) {
    for (var c = clazz; c; c = c.getSuperclass()) {
      try { var f = c.getDeclaredField(name); f.setAccessible(true); return f.get(obj); } catch (e) {}
    }
    return null;
  }
  // The gateway E-Sys is connected to. A gateway-URL connection keeps it in ConnectionProperties.url; a VIN connection
  // doesn't: E-Sys picks the car from the vehicles PsdZ found (ConnectionLogic.connectionVin) by its id
  // (gatewayVin / vehicleInfo, e.g. "WBA..._DIAGADR10"), and that VehicleId knows the URL.
  // Every address the car E-Sys is connected to may have: the same car can be found on more than one network adapter
  // (a VPN or virtual adapter next to the ENET one), so the caller tries them in turn. lookup = may ask PsdZ to search
  // the network when E-Sys has no list (slow: never on Swing's thread).
  function esysUrls(lookup) {
    var CL = cls("com.bmw.esys.domain.control.ConnectionLogic"), logic = null, cp = null;
    try { logic = CL ? CL.getMethod("getInstance").invoke(null) : null; } catch (e) {}
    try { if (logic && !logic.isConnected()) return []; } catch (e) {}
    try { cp = logic ? logic.getLastConnection() : null; } catch (e) {}
    if (!cp) {
      var C = cls("com.bmw.esys.gui.control.EsysController"), inst = C ? getField(null, C, "instance") : null;
      cp = inst ? getField(inst, C, "currentConnection") : null;
    }
    if (!cp) return [];
    try { if (cp.getUrl()) return [String(cp.getUrl())]; } catch (e) {}
    var want = [];
    try { if (cp.getGatewayVin()) want.push(String(cp.getGatewayVin())); } catch (e) {}
    try { if (cp.getVehicleInfo()) want.push(String(cp.getVehicleInfo())); } catch (e) {}
    function urls(ids) {
      ids = ids ? Java.from(ids) : [];
      var hits = ids.filter(function (v) {
        var s = String(v.getAsString()), id = String(v.getId());
        return want.some(function (w) { return w === s || w === id || s.indexOf(w) === 0; });
      });
      if (!hits.length && ids.length === 1) hits = ids;
      var out = [];
      hits.forEach(function (v) {
        var u = null;
        try { u = v.getURL() ? String(v.getURL()) : null; } catch (e) {}
        try { if (!u) u = "tcp://" + v.getInetAddress().getHostAddress() + ":6801"; } catch (e) {}
        if (u && out.indexOf(u) < 0) out.push(u);
      });
      return out;
    }
    var found = logic ? urls(getField(logic, CL, "connectionVin")) : [];
    if (found.length || !lookup) return found;
    try {   // nothing cached: ask PsdZ for the vehicles on the network, as E-Sys's Open Connection dialog does
      var F = cls("com.bmw.esys.domain.psdz.PsdzConnectionFactory");
      return F ? urls(F.getMethod("getInstance").invoke(null).requestAvailableVehicles()) : [];
    } catch (e) { return []; }
  }
  function esysUrl() { return esysUrls(false)[0] || null; }

  // A car's gateway takes one tester connection at a time, so while E-Sys is connected DTC borrows E-Sys's: it closes
  // it with E-Sys's own ConnectionLogic, does its work, and reopens it (same settings: PsdzConnection keeps the builder).
  function esysLogic() {
    var CL = cls("com.bmw.esys.domain.control.ConnectionLogic");
    try { return CL ? CL.getMethod("getInstance").invoke(null) : null; } catch (e) { return null; }
  }
  function esysBusy() {   // coding, flashing and E-Sys's other long operations show a modal progress dialog
    var ws = Awt.Window.getWindows();
    for (var i = 0; i < ws.length; i++) if (ws[i] instanceof Awt.Dialog && ws[i].isModal() && ws[i].isShowing()) return true;
    return false;
  }
  function borrow() {
    var logic = esysLogic();
    try { if (!logic || !logic.isConnected()) return null; } catch (e) { return null; }
    logic.closeConnection();
    log("DTC: E-Sys's connection closed for DTC");
    return logic;
  }
  function giveBack(logic) {
    if (!logic) return true;
    try { logic.reopenConnection(); } catch (e) { log("DTC: reopening E-Sys's connection failed: " + e); }
    var ok = false;
    try { ok = !!logic.isConnected(); } catch (e) {}
    log("DTC: E-Sys's connection " + (ok ? "reopened" : "NOT reopened"));
    return ok;
  }

  // a session with the first address where modules answer: {s, addrs, url}; tries rounds times (the gateway can take a
  // moment to let go of E-Sys's connection)
  function connectAny(urls, rounds) {
    var last = "no gateway";
    for (var r = 0; r < (rounds || 1); r++) {
      if (r) java.lang.Thread.sleep(700);
      for (var i = 0; i < urls.length; i++) {
        var s = null;
        try {
          s = new Session(urls[i]);
          var addrs = discover(s);
          if (addrs.length) return { s: s, addrs: addrs, url: urls[i] };
          last = "no module answered at " + urls[i];
        } catch (e) {
          last = urls[i] + ": " + e;
        }
        if (s) s.close();
      }
    }
    throw last;
  }
  // {address: base variant} from E-Sys's SVT Actual (Coding), when one is loaded
  function svtNames() {
    var names = {}, C = cls("com.bmw.esys.gui.control.EsysController"), CMP = cls("com.bmw.esys.gui.svt.SvtCompareController");
    var inst = C ? getField(null, C, "instance") : null, mods = inst ? getField(inst, C, "cacheModules") : null;
    if (!mods || !CMP) return names;
    var queue = [], seen = new java.util.IdentityHashMap(), it = mods.values().iterator();
    while (it.hasNext()) queue.push({ o: it.next(), d: 0 });
    while (queue.length) {
      var e = queue.shift(), o = e.o;
      if (!o || seen.containsKey(o)) continue;
      seen.put(o, true);
      if (CMP.isInstance(o)) {
        try {
          var m = getField(o, CMP, "logicSvtActual").getModel();
          Java.from(m.getStandardSVT().getEcu()).forEach(function (ecu) {
            Java.from(ecu.getDiagnosticAddresses().getDiagnosticAddress()).forEach(function (d) {
              names[d.getPhysicalOffset()] = String(ecu.getBaseVariant());
            });
          });
        } catch (eS) {}
        continue;
      }
      if (e.d >= 3) continue;
      for (var c = o.getClass(); c && String(c.getName()).indexOf("com.bmw.esys.gui") === 0; c = c.getSuperclass()) {
        var fs = c.getDeclaredFields();
        for (var i = 0; i < fs.length; i++) {
          if (fs[i].getType().isPrimitive() || java.lang.reflect.Modifier.isStatic(fs[i].getModifiers())) continue;
          try {
            fs[i].setAccessible(true);
            var v = fs[i].get(o);
            if (v && String(v.getClass().getName()).indexOf("com.bmw.esys.gui") === 0) queue.push({ o: v, d: e.d + 1 });
          } catch (eF) {}
        }
      }
    }
    return names;
  }

  // ------------------------------------------------------------------ the view
  function build() {
    var root = new Sw.JPanel(new Awt.BorderLayout(0, 6));
    root.setBorder(Sw.BorderFactory.createEmptyBorder(4, 4, 4, 4));
    var top = new Sw.JPanel(new Awt.GridBagLayout());
    top.setBorder(Sw.BorderFactory.createTitledBorder("DTC"));
    var GBC = Awt.GridBagConstraints;
    function gbc(x, y, wx, w) {
      var c = new GBC(); c.gridx = x; c.gridy = y; c.weightx = wx; c.gridwidth = w || 1;
      c.fill = wx ? GBC.HORIZONTAL : GBC.NONE; c.insets = new Awt.Insets(2, 4, 2, 4); c.anchor = GBC.WEST; return c;
    }
    var gw = new Sw.JTextField(esysUrl() || "", 22);
    var bFromEsys = new Sw.JButton("From E-Sys"), bRead = new Sw.JButton("Read faults"), bClear = new Sw.JButton("Clear all faults...");
    var info = new Sw.JCheckBox("Also clear info memory", false);
    var status = new Sw.JLabel("Connect E-Sys to the car, then Read faults.");
    top.add(new Sw.JLabel("Gateway"), gbc(0, 0, 0)); top.add(gw, gbc(1, 0, 0.4)); top.add(bFromEsys, gbc(2, 0, 0));
    top.add(bRead, gbc(3, 0, 0)); top.add(bClear, gbc(4, 0, 0)); top.add(info, gbc(5, 0, 0));
    top.add(status, gbc(0, 1, 1, 6));
    root.add(top, Awt.BorderLayout.NORTH);

    var rows = [], COLS = ["Module", "Address", "Fault code", "Status", "Description"];
    var model = new Sw.table.AbstractTableModel({
      getRowCount: function () { return rows.length; },
      getColumnCount: function () { return COLS.length; },
      getColumnName: function (c) { return COLS[c]; },
      getValueAt: function (r, c) { return rows[r][c]; },
      isCellEditable: function () { return false; }
    });
    // a plain JTable: E-Sys's look and feel has no UI for a script subclass of it (nothing would be painted)
    var table = new Sw.JTable(model);
    table.addMouseMotionListener(new Awt.event.MouseMotionAdapter({
      mouseMoved: function (ev) {
        var r = table.rowAtPoint(ev.getPoint()), x = r >= 0 ? rows[r] : null;
        table.setToolTipText(x && x.others && x.others.length ? "<html>Also:<br>" + x.others.map(function (o) {
          return "&bull; " + String(o).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }).join("<br>") + "</html>" : null);
      }
    }));
    table.setRowHeight(table.getRowHeight() + 4);
    table.getColumnModel().getColumn(0).setPreferredWidth(210);
    table.getColumnModel().getColumn(1).setPreferredWidth(55);
    table.getColumnModel().getColumn(2).setPreferredWidth(75);
    table.getColumnModel().getColumn(3).setPreferredWidth(65);
    table.getColumnModel().getColumn(4).setPreferredWidth(520);
    var base = new Sw.table.DefaultTableCellRenderer();   // delegate: colours are set per cell below
    table.setDefaultRenderer(java.lang.Object.class, new Sw.table.TableCellRenderer({
      getTableCellRendererComponent: function (t, v, sel, foc, r, c) {
        var comp = base.getTableCellRendererComponent(t, v, sel, foc, r, c), x = rows[r];
        if (!sel) comp.setForeground(c === 3 ? (v === "present" ? RED : v === "stored" ? ORANGE : GREY)
                                      : x && x.kind === "none" ? GREEN : x && x.kind === "error" ? GREY : t.getForeground());
        return comp;
      }
    }));
    root.add(new Sw.JScrollPane(table), Awt.BorderLayout.CENTER);

    var busy = false;
    function setBusy(b, msg) {
      busy = b;
      [bRead, bClear, bFromEsys, gw, info].forEach(function (x) { x.setEnabled(!b); });
      if (msg) status.setText(msg);
    }
    function ui(fn) { Sw.SwingUtilities.invokeLater(new java.lang.Runnable({ run: fn })); }
    function fill(results, names) {
      rows = [];
      var total = 0;
      function row(cells, kind, others) { cells.kind = kind; cells.others = others || []; rows.push(cells); }
      results.forEach(function (m) {
        var name = names[m.addr] || moduleTitle(m.addr), a = "0x" + hex2(m.addr);
        if (m.error) { row([name, a, "", "", m.error], "error"); return; }
        if (!m.faults.length) { row([name, a, "", "", "No faults"], "none"); return; }
        m.faults.forEach(function (f) {
          total++;
          row([name, a, f.code, f.state, f.text || "-"], "fault", f.others);
        });
      });
      // modules with faults first, then the rest, each by address
      rows.sort(function (x, y) {
        var kx = x.kind === "fault" ? 0 : x.kind === "error" ? 1 : 2, ky = y.kind === "fault" ? 0 : y.kind === "error" ? 1 : 2;
        return kx - ky || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0);
      });
      model.fireTableDataChanged();
      return total;
    }
    // An address typed in the field is used as it is; an empty field, or one filled from E-Sys, follows E-Sys's
    // connection and tries each address the car was found at.
    var fromEsys = !!String(gw.getText()).trim();
    gw.getDocument().addDocumentListener(new Sw.event.DocumentListener({
      insertUpdate: function () { if (!filling) fromEsys = false; },
      removeUpdate: function () { if (!filling) fromEsys = false; },
      changedUpdate: function () {}
    }));
    var filling = false;
    function setGateway(u) { filling = true; try { gw.setText(u); } finally { filling = false; } fromEsys = true; }
    function work(label, job) {
      if (busy) return;
      var typed = String(gw.getText()).trim();
      if (typed && !fromEsys && !parseUrl(typed)) { status.setText("Not a gateway address: " + typed); return; }
      if (esysBusy()) { status.setText("E-Sys is busy."); return; }
      setBusy(true, label + "...");
      new java.lang.Thread(function () {
        var c = null, borrowed = null;
        try {
          var urls = typed && !fromEsys ? [typed] : esysUrls(true);    // before borrowing: needs E-Sys connected
          if (!urls.length) { ui(function () { status.setText("Connect E-Sys to the car first."); }); return; }
          borrowed = borrow();
          ui(function () { status.setText("Finding the car's modules..."); });
          c = connectAny(urls, borrowed ? 5 : 1);
          if (fromEsys || !typed) ui(function () { setGateway(c.url); });
          job(c.s, c.addrs);
        } catch (e) {
          log("DTC: " + e);
          ui(function () { status.setText("No connection: " + e); });
        } finally {
          if (c) c.s.close();
          if (borrowed && !giveBack(borrowed))
            ui(function () { status.setText(status.getText() + " E-Sys's connection couldn't be reopened: use Open Connection."); });
          ui(function () { setBusy(false); });
        }
      }, "ocm-dtc").start();
    }
    function readInto(s, addrs, prefix) {
      var names = svtNames();
      var res = readAll(s, addrs, function (i, n, a) {
        ui(function () { status.setText("Reading " + (names[a] || moduleTitle(a) || "0x" + hex2(a)) + " (" + (i + 1) + " of " + n + ")"); });
      });
      ui(function () {
        var total = fill(res, names);
        status.setText((prefix || "") + total + " fault" + (total === 1 ? "" : "s") + " in " + addrs.length + " modules.");
      });
      log("DTC: read " + addrs.length + " modules at " + s.where + ", " +
          res.reduce(function (n, m) { return n + (m.faults ? m.faults.length : 0); }, 0) + " faults");
    }
    bFromEsys.addActionListener(function () {
      var u = esysUrl();
      if (u) { setGateway(u); status.setText("Gateway from E-Sys: " + u); }
      else if (esysUrls(false).length === 0) { setGateway(""); status.setText("E-Sys isn't connected to a car."); }
    });
    bRead.addActionListener(function () { work("Reading", function (s, addrs) { readInto(s, addrs); }); });
    bClear.addActionListener(function () {
      var msg = "Clear all faults" + (info.isSelected() ? " and the info memory" : "") + "?";
      if (Sw.JOptionPane.showConfirmDialog(root, msg, "Clear all faults", Sw.JOptionPane.OK_CANCEL_OPTION,
                                           Sw.JOptionPane.QUESTION_MESSAGE) !== Sw.JOptionPane.OK_OPTION) return;
      var withInfo = info.isSelected();
      work("Clearing", function (s, addrs) {
        var r = clearAll(s, addrs, withInfo);
        log("DTC: cleared " + r.cleared + " of " + addrs.length + " modules at " + s.where +
            (r.refused.length ? ", refused: " + r.refused.join(" ") : "") + (r.silent.length ? ", no answer: " + r.silent.join(" ") : ""));
        var head = "Cleared " + r.cleared + " of " + addrs.length + " modules" +
                   (r.refused.length ? "; refused by " + r.refused.join(", ") : "") +
                   (r.silent.length ? "; no answer from " + r.silent.join(", ") : "") + ". Read again: ";
        java.lang.Thread.sleep(1500);                    // let the modules re-run their checks before reading back
        readInto(s, addrs, head);
      });
    });
    texts();
    return root;
  }

  return { build: build, Session: Session, discover: discover, readAll: readAll, readOne: readOne, clearAll: clearAll,
           textFor: textFor, moduleTitle: moduleTitle, statusText: statusText, parseUrl: parseUrl, svtNames: svtNames,
           esysUrl: esysUrl, esysUrls: esysUrls, connectAny: connectAny, borrow: borrow, giveBack: giveBack, esysBusy: esysBusy };
});
