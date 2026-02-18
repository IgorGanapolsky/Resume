
    
      You need to enable JavaScript to run this app.
    
    Software Engineer, Backend and Infrastructure LocationMiamiAddressMiami, FloridaEmployment TypeFull timeLocation TypeOn-siteDepartmentEngineeringOverviewApplicationRole As a Software Engineer, Backend and Infrastructure, you will build the mission-critical backend powering our medical AI platform used by healthcare providers worldwide. You will architect and scale core systems, including service reliability and data platforms with autonomy to shape performance, security, and infrastructure design for sensitive medical data. We hire exceptional builders, not in narrow lanes. Engineers work across products and projects, owning work wherever they can create the most impact.About Us OpenEvidence is the most widely used medical AI platform in the world. In just over a year, our usage has grown to >40% of US clinicians via product-led word of mouth adoption. We are a $12B company with a 30-person engineering team from MIT, Harvard, and Stanford. We believe world-changing products come from a small group of exceptional, autonomous builders, organized along focused objectives and empowered to take individual ownership and run fast. We are growing our team to seize a once-in-a-lifetime opportunity to define the default platform for medical AI.If you are a world-class engineer or scientist looking to define the bleeding edge and deliver concrete outcomes that impact hundreds of millions of lives, we want to talk.CultureWe believe work should be engaged with at a world-class level. Building 0->1 and scaling 1->1000 are professional sports, and uncompromising excellence is the bar. We believe building technologies that haven't existed is only possible with end-to-end ownership. Important things are accomplished when an individual decides to accomplish them.Who are you?If you are looking to complete tickets 9-5, this job is not for you. If you are looking to write papers, this job is not for you. If you are looking to lean in and get your hands dirty, bruised, and bloody, making something from nothing, personally impacting hundreds of millions of lives, and driving ten figures of revenue, this job might be for you.The ideal candidate is a brilliant builder. They are smart, ambitious, scrappy, autonomous, precise, motivated, hard-working, and low-ego. Does that sound rare? It is rare, we have only found 30 of them, and we would like to find more.LocationThis full-time role on our engineering team is in-person 5 days a week in Miami.Apply for this JobThis site is protected by reCAPTCHA and the Google Privacy Policy and Terms of Service apply.Powered by AshbyPrivacy PolicySecurityVulnerability Disclosure
    
<!-- [script/app-data removed by scrub_job_captures.py] -->
      fetch("https://cdn.ashbyprd.com/frontend_non_user/1083eac393d4da12d6ff74cf138862e43115ccb5/.vite/manifest.json").then(function (res) { return res.json() }).then(function (manifest) {
        const indexData = manifest["index.html"];
    
        let bundleLoaded = false;
        function loadBundle() {
          if (bundleLoaded === true) {
            return;
          }
    
          const el = document.createElement("script");
          el.setAttribute("type", "module");
          el.setAttribute("crossorigin", "");
          el.setAttribute("integrity", indexData.integrity);
          el.setAttribute("src", "https://cdn.ashbyprd.com/frontend_non_user/1083eac393d4da12d6ff74cf138862e43115ccb5/" + indexData.file);
          document.head.appendChild(el);
          bundleLoaded = true;
        }
    
        if (indexData.css != null && indexData.css.length > 0) {
          const loadedSheets = [];
          indexData.css.forEach(function (sheet) {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.type = "text/css";
            link.href = "https://cdn.ashbyprd.com/frontend_non_user/1083eac393d4da12d6ff74cf138862e43115ccb5/" + sheet;
            link.media = "all";
            link.onload = function () {
              loadedSheets.push(sheet);
              if (loadedSheets.length === indexData.css.length) {
                loadBundle();
              }
            };
            link.onerror = loadBundle;
            document.head.insertBefore(link, document.getElementById("vite-preload"));
          });
          const preload = document.createElement("link");
          preload.rel = "modulepreload";
          preload.href = "https://cdn.ashbyprd.com/frontend_non_user/1083eac393d4da12d6ff74cf138862e43115ccb5/" + indexData.file;
          document.head.appendChild(preload);
        } else {
          loadBundle();
        }
    
        if (indexData.imports != null && indexData.imports.length > 0) {
          indexData.imports.forEach(function (file) {
            const preload = document.createElement("link");
            preload.rel = "modulepreload";
            preload.href = "https://cdn.ashbyprd.com/frontend_non_user/1083eac393d4da12d6ff74cf138862e43115ccb5/" + manifest[file].file;
            document.head.appendChild(preload);
          });
        }
      });
      

