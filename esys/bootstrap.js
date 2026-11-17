// Open CAFD Map: entry point for E-Sys started by OpenLauncher (ocm\esys.py launch).
//
// OpenLauncher runs E-Sys's own JRE + classpath with Java's JavaScript runner (jdk.nashorn.tools.Shell)
// as the main class. This script loads the labelling agent, then starts E-Sys exactly as E-Sys.bat
// does (com.bmw.esys.gui.control.Framework). Nothing is installed into E-Sys; an E-Sys started any
// other way never runs this code.
(function () {
  var System = java.lang.System;
  if (String(System.getProperty("ocm.launcher")) !== "1") {
    System.err.println("Open CAFD Map: bootstrap.js must be started by OpenLauncher.");
    System.exit(2);
  }

  // The agent is optional for E-Sys: any problem loading it is logged and E-Sys starts unlabelled.
  try {
    load(String(System.getProperty("ocm.agent")));
  } catch (e) {
    try {
      var w = new java.io.FileWriter(String(System.getProperty("ocm.log")), true);
      w.write("[bootstrap] agent failed to load, starting E-Sys without labels: " + e + "\r\n");
      w.close();
    } catch (e2) {}
  }

  function note(msg) {
    try {
      var w = new java.io.FileWriter(String(System.getProperty("ocm.log")), true);
      w.write("[" + new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS").format(new java.util.Date()) + "] " + msg + "\r\n");
      w.close();
    } catch (e) {}
  }

  // OpenLauncher > Settings > E-Sys language (ocm.esysLanguage = en or de). E-Sys picks its language once while it
  // starts, from language= in config\Esys.properties (LocaleUtils.initLanguage). It's set in E-Sys's settings in
  // memory for that start-up, and E-Sys's own value goes back once its window is up, so E-Sys's Options dialog shows
  // (and saves) what's in the file. Esys.properties is never written.
  var LANG = String(System.getProperty("ocm.esysLanguage") || "");
  if (LANG === "en" || LANG === "de") {
    try {
      var PM = Java.type("com.bmw.esys.commons.PropertyManager");
      var own = PM.getProperty("language");
      PM.setProperty("language", LANG);
      note("E-Sys language for this start: " + LANG + " (Esys.properties has " + (own ? own : "none") + ")");
      var restore = new java.lang.Thread(function () {
        try {
          for (var i = 0; i < 600; i++) {               // up to 5 minutes
            java.lang.Thread.sleep(500);
            var up = Java.from(java.awt.Frame.getFrames()).some(function (f) {
              return f.isShowing() && String(f.getTitle() || "").indexOf("E-Sys") === 0;
            });
            if (!up) continue;
            // straight into E-Sys's settings, without a change event (nothing should react to it)
            var fld = PM.class.getDeclaredField("esysPropertiesApp");
            fld.setAccessible(true);
            var props = fld.get(null);
            if (own) props.setProperty("language", own); else props.remove("language");
            note("E-Sys language: E-Sys's window is up in " + java.util.Locale.getDefault().getLanguage()
                 + ", its own setting is back in memory (" + PM.getProperty("language") + ")");
            return;
          }
        } catch (e) { note("E-Sys language: couldn't put E-Sys's own setting back: " + e); }
      }, "ocm-language");
      restore.setDaemon(true);
      restore.start();
    } catch (e) { note("E-Sys language: couldn't set " + LANG + ", E-Sys uses its own setting: " + e); }
  }

  // E-Sys's main class comes from its own E-Sys.bat (ocm\esys.py). If E-Sys fails while starting, log why and
  // exit: otherwise Java's GUI thread keeps the process alive and the splash screen stays up forever.
  var mainClass = String(System.getProperty("ocm.main") || "com.bmw.esys.gui.control.Framework");
  try {
    Java.type(mainClass).main(Java.to([], "java.lang.String[]"));
  } catch (e) {
    var msg = "[bootstrap] E-Sys " + (System.getProperty("ocm.esysVersion") || "") + " failed to start (" + mainClass + "): " + e;
    System.err.println(msg);
    if (e instanceof java.lang.Throwable) e.printStackTrace();
    try {
      var w2 = new java.io.FileWriter(String(System.getProperty("ocm.log")), true);
      w2.write(msg + "\r\n");
      w2.close();
    } catch (e3) {}
    System.exit(3);
  }
})();
