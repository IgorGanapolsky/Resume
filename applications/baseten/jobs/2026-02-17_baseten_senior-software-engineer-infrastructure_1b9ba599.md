
    
      You need to enable JavaScript to run this app.
    
    Senior Software Engineer - Infrastructure LocationSan Francisco, New York, RemoteEmployment TypeFull timeLocation TypeHybridDepartmentEPDEngineeringInfrastructureCompensation$200K – $270KCompetitive compensation. We aim to provide 90th percentile (or better) salaries and equity grants for every team member commensurate with their experience.OverviewApplicationABOUT BASETENBaseten powers mission-critical inference for the world's most dynamic AI companies, like Cursor, Notion, OpenEvidence, Abridge, Clay, Gamma and Writer. By uniting applied AI research, flexible infrastructure, and seamless developer tooling, we enable companies operating at the frontier of AI to bring cutting-edge models into production. We're growing quickly and recently raised our $300M Series E, backed by investors including BOND, IVP, Spark Capital, Greylock, and Conviction. Join us and help build the platform engineers turn to to ship AI products.THE ROLEAs a Senior Infrastructure Software Engineer at Baseten, you'll architect and lead development of our ML inference platform that powers production AI applications. You'll make key technical decisions for the infrastructure enabling developers to deploy, scale, and monitor ML models with high performance and reliability.EXAMPLE INITIATIVESYou'll get to work on these types of projects as part of our Infrastructure team: Multi-cloud capacity managementInference on B200 GPUsMulti-node inferenceFractional H100 GPUs for efficient model servingRESPONSIBILITIESDesign and architect scalable infrastructure systems for our ML inference platformLead optimization of Kubernetes deployments for efficient, cost-effective model servingDrive enhancements to our inference orchestration layer for complex model deploymentsDefine monitoring strategies for model performance, latency, and resource utilizationDevelop advanced solutions for GPU capacity management and throughput optimizationEstablish infrastructure automation standards to streamline ML deployment workflowsPartner with other engineers to translate complex inference requirements into technical solutionsMake critical architectural decisions balancing performance with system reliabilityLead technical discussions and mentor junior engineers on infrastructure best practicesContribute to long-term technical strategy and infrastructure roadmapREQUIREMENTSBachelor's degree or higher in Computer Science or related field5+ years experience building production infrastructure systemsExpert-level proficiency in Go, with Python experience a plusDeep expertise with Kubernetes in production environmentsExtensive experience with major cloud providers (AWS, GCP) and neo-cloud providers (Crusoe, DigitalOcean, Nebius) a plus.Advanced understanding of distributed systems concepts and performance tuningProven experience designing observability systemsTrack record of leading technical initiatives and mentoring engineersExperience with ML/AI workloads and MLOps platforms highly valuedBENEFITSCompetitive compensation, including meaningful equity.100% coverage of medical, dental, and vision insurance for employee and dependentsGenerous PTO policy including company wide Winter Break (our offices are closed from Christmas Eve to New Year's Day!)Paid parental leaveCompany-facilitated 401(k)Exposure to a variety of ML startups, offering unparalleled learning and networking opportunities.Apply now to embark on a rewarding journey in shaping the future of AI! If you are a motivated individual with a passion for machine learning and a desire to be part of a collaborative and forward-thinking team, we would love to hear from you.At Baseten, we are committed to fostering a diverse and inclusive workplace. We provide equal employment opportunities to all employees and applicants without regard to race, color, religion, gender, sexual orientation, gender identity or expression, national origin, age, genetic information, disability, or veteran status.Compensation Range: $200K - $270KApply for this JobThis site is protected by reCAPTCHA and the Google Privacy Policy and Terms of Service apply.Powered by AshbyPrivacy PolicySecurityVulnerability Disclosure
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
      

