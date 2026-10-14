import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def run_scan():
    opts = Options()
    opts.add_argument('--headless=new')
    driver = webdriver.Chrome(options=opts)
    driver.set_script_timeout(300)
    
    print("Navigating to base url...")
    driver.get('https://admin2.formaturamaxi.com.br/')
    time.sleep(2)

    candidates = set()
    
    # Numbers and numbered prefixes
    for i in range(1, 101):
        candidates.add(str(i))
        candidates.add(f"ppv{i}")
        candidates.add(f"ch{i}")
        candidates.add(f"canal{i}")
        candidates.add(f"event{i}")
        candidates.add(f"evento{i}")
        candidates.add(f"live{i}")
        candidates.add(f"stream{i}")

    # Sports and TV channels common in Spain, LatAm, UK, US, etc.
    bases = [
        # Spanish TV & Sports
        "laliga", "laligatv", "laligahypermotion", "hypermotion",
        "dazn", "daznlaliga", "dazn_laliga", "daznf1", "dazn1", "dazn2", "dazn3", "dazn4",
        "movistar", "mplus", "mlaliga", "m_laliga", "mcampeones", "m_campeones",
        "mdeportes", "m_deportes", "vamos", "m_vamos", "mgolf", "m_golf",
        "eurosport", "eurosport1", "eurosport2", "gol", "goltv", "golplay",
        "teledeporte", "tdp", "tve1", "la1", "la2", "antena3", "cuatro", "telecinco", "lasexta",
        "trece", "mega", "fdf", "energy", "divinity", "boing", "neox", "nova", "atreseries",
        "realmadridtv", "rmtv", "barcatv",
        
        # Latin & Brazil
        "tnt", "tntsports", "tnt_sports", "tntseries", "tntnovelas", "space", "warner",
        "sportv", "sport_tv", "sportv1", "sportv2", "sportv3",
        "premiere", "premiere_clubes", "premiere1", "premiere2", "premiere3", "premiere4", "premiere5", "premiere6", "premiere7", "premiere8", "premiere9",
        "combate", "bandsports", "band", "globo", "sbt", "record", "redetv", "cultura", "cazetv", "canaloff",
        "espn", "espn1", "espn2", "espn3", "espn4", "espn5", "espn6", "espn7", "espnbrasil", "espn_brasil", "espnextra", "espn_extra",
        "paramount", "paramountplus", "telecine", "telecine_premium", "telecine_action", "telecine_pipoca",
        "hbo", "hbo2", "hbo_plus", "hbo_family", "universal", "megapix", "multishow", "gnt", "viva", "globonews",
        
        # International
        "bein", "beinsports", "beinsport", "bein1", "bein2", "bein3", "bein4", "bein5",
        "sky", "skysports", "skysportsf1", "skysportsfootball", "skysportsmainevent", "skysportspremierleague",
        "eleven", "elevensports", "canalplus", "canalplussport", "rmcsport", "rmcsport1", "rmcsport2",
        "f1", "formula1", "motogp", "nba", "nfl", "ufc", "wwe",
        "fox", "foxsports", "foxsports1", "foxsports2", "fox1", "fox2",
        "tyc", "tycsports", "dsports", "directv", "win", "winsports",
        "clarosports", "tudn", "azteca7", "azteca1", "lasestrellas", "canal5"
    ]

    for b in bases:
        candidates.add(b)
        for i in range(1, 15):
            candidates.add(f"{b}{i}")
            candidates.add(f"{b}_{i}")
        candidates.add(f"{b}hd")
        candidates.add(f"{b}_hd")
        candidates.add(f"{b}fhd")

    candidates_list = sorted(list(candidates))
    print(f"Total candidate stream names to scan: {len(candidates_list)}")

    script = """
    const names = arguments[0];
    const done = arguments[1];
    const results = {};

    let idx = 0;
    const concurrency = 40;

    async function worker() {
        while (idx < names.length) {
            const current = idx++;
            const name = names[current];
            try {
                const url = 'https://admin2.formaturamaxi.com.br/' + name + '.m3u8';
                const res = await fetch(url, { method: 'GET' });
                if (res.status === 200) {
                    const text = await res.text();
                    if (text.includes('#EXTM3U')) {
                        results[name] = {
                            url: url,
                            sample: text.slice(0, 120)
                        };
                    }
                }
            } catch (e) {}
        }
    }

    const workers = [];
    for (let i = 0; i < concurrency; i++) {
        workers.push(worker());
    }

    Promise.all(workers).then(() => done(results));
    """

    t0 = time.time()
    results = driver.execute_async_script(script, candidates_list)
    t1 = time.time()
    print(f"Scan finished in {t1 - t0:.2f}s. Found {len(results)} valid channels!")
    
    with open('/Users/antonio-bravo/git/m3u/scratch/scan_results.json', 'w') as f:
        json.dump(results, f, indent=2)

    print("Found channels:")
    for k in sorted(results.keys()):
        print(f" - {k}")

    driver.quit()

if __name__ == '__main__':
    run_scan()
