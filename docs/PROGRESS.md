# Evidence-Gap Investigator — İlerleme Raporu

Son güncelleme: 2026-10-17 (UTC)

## Durum özeti

| Aşama | Durum |
|---|---|
| 1. Veritabanı şeması + sentetik veri | ✅ Tamamlandı (Mina'nın MySQL'inde yüklendi ve sayılar doğrulandı) |
| 2. LLM sağlayıcı katmanı + duman testi | ✅ Tamamlandı. Gerçek Gemini duman testi Mina'nın bilgisayarında 4/4 geçti |
| 3. Araçlar (`get_incident`, `get_evidence`, `find_similar_incidents`) | ✅ Tamamlandı. Mina'nın bilgisayarında 55/55 test ve duman testi 4/4 geçti |
| 4. Agent döngüsü + yapılandırılmış sonuç | 🟡 Kod, doğrulama ve değerlendirme hazır, bulutta mock testleriyle 96/96 geçti. Mina'nın bilgisayarında testler OK. Gerçek Gemini ile 3 olay araştırıldı, üçü de değerlendirmede PASS aldı (ayrıntılar aşağıda). Toplam 7 model isteği |
| 5. FastAPI, 6. React arayüzü | ⏳ Başlanmadı |

## 4. adım — Agent döngüsü (mock aşaması, 2026-10-17)

### Yeni ve değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `investigator/validation.py` | **Yeni.** `submit_investigation` çıktısının katı doğrulaması |
| `investigator/investigation.py` | **Yeni.** Sınırlı agent döngüsü (`run_investigation`), `SUBMIT_INVESTIGATION` tanımı, sistem talimatı ve `save_run` |
| `scripts/investigate.py` | **Yeni.** Tek bir olayı gerçek LLM ile araştırır ve sonucu `runs/` klasörüne yazar. **Henüz çalıştırılmadı** |
| `scripts/evaluate.py` | **Yeni.** Çalıştırma sonuçlarını `expected_outcomes.json` ile karşılaştırır. Bu dosyayı okuyan tek kod bu |
| `tests/fakes.py` | **Yeni.** API kullanmayan, senaryoya göre cevap veren sahte LLM sağlayıcısı |
| `tests/test_validation.py` | **Yeni.** 22 çevrimdışı test |
| `tests/test_investigation_loop.py` | **Yeni.** 13 çevrimdışı test (sahte araçlar) |
| `tests/test_investigation_e2e.py` | **Yeni.** 5 test: sahte model, gerçek araçlar, salt-okunur MySQL ve değerlendirme |
| `tests/test_security.py` | +1 test: `scripts/` içinde fixture dosyasını yalnızca `evaluate.py` okuyabilir |
| `tests/fixtures/expected_outcomes.json` | INC-1004 için beklenen sonraki kontrollere anahtar kelime grupları ve Redis anahtar kelimesi eklendi, eski serbest metin listesi kaldırıldı |
| `.gitignore` | `runs/` eklendi |

Önceki adımların dosyalarına (`tools.py`, `gemini.py`, `smoke_test.py` …) dokunulmadı.

### Doğrulama kuralları (`validation.py`)

Modelin gönderdiği sonuç doğrudan kabul edilmez. Her kural ihlali okunabilir bir mesaj olarak listelenir.

- **Yapı:** Tüm alanlar zorunlu. Eksik ya da beklenmeyen alanlar reddedilir (üst seviye, hipotez, kanıt referansı ve sonraki kontrol nesnelerinde). Tipler kontrol edilir. Metin alanları boş olamaz ve en fazla 2000 karakter olabilir. En fazla 6 hipotez ve 10 sonraki kontrol kabul edilir. Liste ya da sözlük gibi beklenmedik değerler programı çökertmez, ihlal olarak raporlanır (test edildi).
- **Enum değerleri:** `verdict`, `confidence`, `status` ve `root_cause_category` yalnızca izin verilen değerleri alabilir.
- **Kimlikler:** Hipotez kimlikleri `H1`, `H2` … biçiminde ve benzersiz olmalı. `incident_key` hedef olayla aynı olmalı.
- **Kanıt:** Atıf yalnızca **bu araştırmada** `get_evidence` aracının döndürdüğü `E-` kimliklerine yapılabilir. `GAP-` kimliği destekleyen ya da çelişen kanıt olarak her zaman reddedilir ve yalnızca `next_checks.based_on_gaps` alanında kabul edilir. Aynı kanıtın listede iki kez geçmesi ya da bir hipotezde hem destekleyen hem çelişen olarak geçmesi reddedilir.
- **Geçmiş olaylar:** `related_history` yalnızca bu araştırmada `find_similar_incidents` aracının döndürdüğü olayları içerebilir. Hedef olayın kendisi yazılamaz. `medium` ya da `high` güven için **bu olaydan** en az bir destekleyen kanıt gerekir, yani geçmiş olaylar tek başına sonucu belirleyemez.
- **Durum ve güven:** `ruled_out` için `observation` türünde en az bir çelişen kanıt gerekir ve güven `low` olmalıdır. `negative_finding` tek başına bir hipotezi eleyemez. `weakened` için en az bir çelişen kanıt gerekir. En fazla bir hipotez `leading` olabilir.
- **Sonuç:** `conclusive` için tam olarak bir `leading` hipotez gerekir, güveni `high` olmalı ve en az bir destekleyen kanıtı olmalı. `inconclusive` sonuçta hiçbir hipotezin güveni `high` olamaz ve `next_checks` boş olamaz.

Önceki onaylı tasarıma eklediğim üç küçük kural: `ruled_out` olan hipotezin güveni `low` olmalı, `weakened` için çelişen kanıt gerekli ve `medium`/`high` güven için bu olaydan destekleyen kanıt gerekli. Üçü de "kesinliği abartma" ilkesini uygulamak için.

### Döngü ve sınırlar (`investigation.py`)

| Sınır | Değer | Nasıl uygulanıyor |
|---|---|---|
| Model turu (= 1 `chat()` isteği) | **en fazla 8**, düzeltme turu dahil | Son turda yalnızca `submit_investigation` sunulur ve "son tur" mesajı eklenir |
| Düzeltme | **1** | İlk geçersiz sonuçta ihlal listesi modele gönderilir. İkinci geçersiz sonuçta araştırma `failed` olur ve sebebi `validation_failed` olarak yazılır |
| Tur başına araç çağrısı | en fazla 6 | Fazlası çalıştırılmaz, modele hata döner |
| Toplam araç çağrısı | en fazla 20 | Aynı şekilde |
| Tekrarlanan aynı çağrı | önbellekten | Veritabanına tekrar gidilmez, modele "aynı sonuç" notu döner |
| Rate limit (429) / 5xx | tur başına en fazla 4 HTTP denemesi | `gemini.py` içindeki mevcut sınır |
| Model hatası | araştırma `failed`, sebep `model_error` | Mesajdaki sırlar redakte edilir |
| Araç (DB) hatası | araştırma `failed`, sebep `tool_error` | Aynı şekilde |

Geçersiz bir sonuç hiçbir zaman `investigation` alanına yazılmaz. Reddedilen her deneme `rejected_submissions` listesinde ihlalleriyle birlikte, `submission_REJECTED_NOT_VALID` etiketiyle saklanır.

### Sonuç dosyaları (`runs/`)

`save_run` sonucu `runs/<UTC zaman>_<olay>.json` dosyasına yazar. Var olan bir dosyanın üzerine yazmaz. Son güvence olarak `GEMINI_API_KEY` ve `MYSQL_PASSWORD` değerleri yazmadan önce metinden silinir (test edildi). Dosyada ortam değişkenleri ya da bağlantı bilgisi tutulmaz. `runs/` klasörü `.gitignore` içinde.

### Değerlendirme (`scripts/evaluate.py`)

- Her olay için kontrol edilir: araştırma tamamlandı mı, sonuç (`verdict`) doğru mu, `GAP-` kimliği kanıt olarak gösterilmiş mi. Kesin sonuçlu olaylarda (1003, 1008) `leading` hipotezin kategorisi de kontrol edilir.
- **INC-1004 için ek kontroller:** Hiçbir hipotezin güveni `medium` üstünde olmamalı. Başlığında "redis" geçen hiçbir hipotez `ruled_out` olmamalı. Beklenen dört sonraki kontrolden en az ikisi eşleşmeli. Redis'ten hiç bahsedilmemişse uyarı verilir.
- **Anahtar kelime eşleşmesinin sınırlaması** (raporda her seferinde yazılıyor): Bir sonraki kontrol, beklenen bir maddenin her kelime grubundan en az bir anahtar kelimeyi içeriyorsa eşleşmiş sayılır. Bu yöntem hatalı eşleşme üretebilir (kelime başka bir anlamda geçmişse) ve eşleşmeyi kaçırabilir (eş anlamlı kelime listede yoksa). Skora güvenmeden önce gerçek metne bakılmalı.

### Test sonuçları (bulut, Python 3.14.6, MySQL 8.0.46)

| Test dosyası | Sayı | Sonuç |
|---|---|---|
| `test_validation` | 22 | ✅ |
| `test_investigation_loop` | 13 | ✅ |
| `test_investigation_e2e` | 5 | ✅ |
| `test_tools` | 18 | ✅ |
| `test_security` | 7 | ✅ |
| `test_database` | 16 | ✅ |
| `test_gemini_adapter` | 10 | ✅ |
| `test_smoke_loop` | 5 | ✅ |
| **Toplam** | **96** | ✅ **96/96** |

- Testler hem temiz seed ile (41 kanıt) hem `followup_1004.sql` uygulanmış halde çalıştırıldı, ikisinde de 96/96 geçti. pyflakes temiz.
- **Yakalanan hata:** Yeni bir test, `related_history` alanına liste ya da sözlük gibi bir değer gelirse doğrulamanın çöktüğünü gösterdi (`TypeError: unhashable type`). Düzeltildi.
- **CLI denemesi** (sahte sağlayıcı ile, geçici bir kopyada): `scripts/investigate.py` üç olay için de `completed` döndü, her biri 2 model turu ve 3 araç çağrısı sürdü. Ardından `scripts/evaluate.py` üçünü de PASS verdi ve çıkış kodu 0 oldu. Oluşan `runs/` dosyalarında sır bulunmadı.

**Mock senaryoları** (`test_investigation_e2e`):

| Senaryo | Beklenen | Sonuç |
|---|---|---|
| 1003, 1004 ve 1008 için doğru araştırma (gerçek `E-` kimlikleriyle) | completed + değerlendirme PASS | ✅ |
| 1004: ilk denemede `GAP-` kimliği kanıt olarak gösteriliyor, ikinci denemede düzeltiliyor | 1 ret, sonra completed, sonuçta `GAP-` yok | ✅ |
| 1004: `conclusive` iddiası iki denemede de sürüyor | `failed`, `investigation` boş | ✅ |
| 1004: kurallara uygun ama yanlış (Redis tek bir bellek ölçümüyle eleniyor, tek bir sonraki kontrol var) | Doğrulamadan geçer, **değerlendirme FAIL verir** | ✅ |
| 1003: yanıltıcı INC-1005'e dayanarak `third_party` sonucu | Değerlendirme FAIL verir | ✅ |

### Gerçek Gemini çalıştırmaları (Mina'nın bilgisayarı, 2026-10-17, `gemini-3.8-flash`)

Mina onay vererek komutları kendisi çalıştırdı. Değerlendirme, `runs/` klasöründeki dosyalar üzerinde `scripts/evaluate.py` ile yapıldı.

| Olay | Durum | Model turu | Düzeltme | Sonuç | Değerlendirme |
|---|---|---|---|---|---|
| INC-1003 | completed | 2 | 0 | conclusive, `config_change` (5 kanıt). INC-1005'ten gelen ödeme sağlayıcısı hipotezi `weakened` | PASS 4/4 |
| INC-1004 | completed | 2 | 0 | inconclusive, en yüksek güven medium. Redis hipotezi `weakened`, elenmedi. `GAP-` kimlikleri yalnızca `based_on_gaps` alanında | PASS 6/6 (aşağıdaki nota bakın) |
| INC-1008 | completed | 3 | **1** | conclusive, `expired_credential`. Disk uyarısı `weakened` | PASS 4/4 |

**Toplam 7 model isteği** (tahmin 6–12 idi). 429 hatası alınmadı.

**Gözlemler:**

- **Düzeltme mekanizması gerçek API'de çalıştı.** INC-1008'in ilk teslimatı reddedildi: iki hipotez `weakened` olarak işaretlenmişti ama çelişen kanıt yoktu. Model bir sonraki turda ihlalleri düzeltti.
- **INC-1004'te değerlendirme betiğinin yanlış pozitifi:** Model yalnızca 2 sonraki kontrol önerdi (kayıp loglar, Redis latency ve ağ paketleri), ama betik 4/4 eşleşme saydı. "Pod/node bazında dağılım" kontrolü önerilmemişti. Önerilerden birinin metninde "between auth **pods** and Redis" geçtiği için eşleşmiş sayıldı. Anahtar kelime sınırlamasının somut bir örneği. **Düzeltildi (Mina onayladı):** Pod/node maddesi artık ikinci bir kelime grubu gerektiriyor ("per pod", "breakdown", "distribution", "which pod" …). Ayrıca her öneri en fazla bir beklenen maddeyle eşleşiyor (one-to-one eşleştirme). Yeni `tests/test_evaluate.py` dosyasına bu gerçek çalıştırmanın metniyle 4 test eklendi. Aynı çalıştırmanın yeniden değerlendirmesi: **2/4** (kayıp loglar ve Redis latency). Raporda tahmin ettiğim 3/4 değil, çünkü Redis ve ağ önerileri tek bir maddede birleşmiş ve artık yalnızca bir kez sayılıyor. Eşik 2 olduğu için sonuç yine PASS. Testler: bulutta 100/100, bilgisayardaki Linux ortamında çevrimdışı testler OK.
- **INC-1004'teki diğer zayıf noktalar:** "Redis latency monitor'ü aç" önerisi bu olayın geçmiş verisini geri getirmez. H3'ü (saat kayması) zayıflatırken dayandığı gerekçe bir kanıt değil, modelin çıkarımı ("genelde 401 hatası verir"). H2'yi desteklemek için kullandığı "5xx %8" kaydı çok genel bir kanıt.
- **INC-1003:** "Veritabanı aşırı yüklendi" ihtimali ayrı bir hipotez olarak yazılmadı. Normal DB metrikleri doğrudan H1'in destekleyen kanıtı olarak kullanıldı.

### Tahmini gerçek API kullanımı (çalıştırmalardan önce yapılan tahmin)

| | Model isteği |
|---|---|
| Tipik araştırma (1 tur veri toplama + 1 tur sonuç) | 2 |
| Bir düzeltmeyle | 3–4 |
| Üst sınır (bir araştırma) | 8 |
| **Üç olay (1003, 1004, 1008): beklenen** | **yaklaşık 6–12** |
| Üç olay: üst sınır | 24 başarılı istek. 429 hatalarıyla birlikte en kötü durumda 96 HTTP denemesi |

Her istekte konuşmanın tamamı gönderilir, bu da birkaç bin token eder. Ücretsiz katmanın dakikalık sınırı (yaklaşık 10) için olaylar tek tek çalıştırılmalı.

**Gerçek API ile ilgili açık risk:** `submit_investigation` aracının iç içe JSON şeması gerçek Gemini ile henüz denenmedi. Model şemayı reddederse ya da alanları farklı tiplerde döndürürse doğrulama bunu yakalar ve araştırma `failed` olur, ama ne olacağı ancak gerçek bir çalıştırmada görülecek.

## 3. adım — Araçlar (2026-10-17)

### Değişen ve yeni dosyalar

| Dosya | Değişiklik |
|---|---|
| `investigator/tools.py` | **Yeni.** `get_incident` (smoke_test'ten taşındı, davranışı aynı), `get_evidence`, `find_similar_incidents`, araç tanımları (`TOOL_SPECS`) ve `execute_tool` |
| `scripts/smoke_test.py` | Yalnızca import değişti: `GET_INCIDENT` ve `get_incident` artık `investigator.tools`'tan geliyor. Döngü ve kontroller aynı |
| `tests/test_tools.py` | **Yeni.** 18 test (5 çevrimdışı, 13 salt-okunur DB) |
| `tests/test_security.py` | Kaynak kod kontrolü artık `tools.py`'ye bakıyor. `get_evidence` ve `find_similar_incidents` sorguları için 2 kontrol eklendi (4 → 6 test) |
| `tests/test_database.py` | Yalnızca import değişti: `get_incident` artık `investigator.tools`'tan geliyor |

Başka bir dosyaya dokunulmadı.

### Tasarım kararları

**`get_evidence(incident_key)`**

- Kayıtlar tek bir parametreli sorguyla gelir, `observed_at` ve ardından `id` sırasına göre dizilir. Her kayıt veritabanındaki alanları aynen taşır: içerik, kaynak, `observed_at`, `observed_until`, `collected_at`, `limitations` ve `reliability`. Zamanlar UTC ve `Z` ekli.
- **`data_gap` kayıtları ayrı bir listede (`data_gaps`) döner** ve farklı bir kimlik öneki taşır: kanıtlar `E-<id>`, boşluklar `GAP-<id>`. Her boşlukta "MISSING DATA … not evidence for or against any hypothesis" notu var. 4. aşamada hipotezlerin yalnızca `E-` kimliklerine atıf yapabilmesi bu sayede kolayca doğrulanabilecek.
- `how_to_read` alanı modele kanıt türlerinin anlamını anlatır: `negative_finding` zayıflatır ama çürütmez, `GAP-` kimlikleri asla kanıt olarak gösterilmez.
- Güvenlik sınırı: en fazla 200 satır döner, aşılırsa `truncated: true` olur. Mevcut veri 10 satırın altında.

**`find_similar_incidents(incident_key)`**

- SQL tarafında `status = 'resolved' AND id <> <hedef>` koşulu var. Python tarafında da hedef anahtar ikinci kez dışlanıyor.
- Kök neden yalnızca bu çözülmüş geçmiş olaylar için döner. Hedef olay çözülmüş olsa bile kendi kök nedeni sonuçta görünmez (test edildi).
- **Benzerlik ölçütü:** Aynı servis **veya** en az bir anlamlı ortak başlık kelimesi. Genel kelimeler (`errors`, `failures`, `service`, `api`, `spike` vb.) ve servis adının kendisi kelime eşleşmesinde sayılmaz. Her sonuç `match_reasons` alanında `same_service` ve `shared_title_words` bilgilerini, yanıtın kendisi de `similarity_method` açıklamasını taşır.
- Sıralama: önce aynı servis, sonra ortak kelime sayısı, sonra en yeni olay. En fazla 5 sonuç döner.

**`execute_tool(conn, name, args)`:** Bilinmeyen bir araç ya da yanlış argüman (eksik, fazla, string olmayan) veritabanına gitmeden hata sözlüğü döndürür.

### Örnek sonuçlar (bulut test veritabanı)

| Hedef | Benzer olaylar (sırayla) | Neden |
|---|---|---|
| INC-1003 | INC-1005, INC-1001 | İkisi de aynı servis. INC-1005 ayrıca `502` kelimesini paylaşıyor |
| INC-1004 | INC-1006, INC-1002 | Aynı servis. Ortak kelimeler `intermittent, login` ve `login` |
| INC-1008 | (yok) | Aynı serviste geçmiş olay yok. `failing` kelimesi genel olduğu için tek başına eşleşme sayılmadı |

**Bilinçli bırakılan durum:** INC-1003 için yanıltıcı olay INC-1005, gerçekten benzer olan INC-1001'in önünde çıkıyor, çünkü daha fazla başlık kelimesini paylaşıyor. Araç geçmiş olayları yalnızca listeler. Hangisinin bu olaya uyduğunu kanıtlara bakarak agent'ın (4. aşama) değerlendirmesi gerekecek. Bu senaryo zaten bunu test etmek için tasarlandı.

### Test sonuçları (bulut, Python 3.14.6, MySQL 8.0.46)

| Test dosyası | Sayı | Sonuç |
|---|---|---|
| `test_tools` | 18 | ✅ 18/18 |
| `test_security` | 6 | ✅ 6/6 |
| `test_database` | 16 | ✅ 16/16 |
| `test_gemini_adapter` | 10 | ✅ 10/10 |
| `test_smoke_loop` | 3 + 2 | ✅ 5/5 |
| **Toplam** | **55** | ✅ **55/55** |

Testler iki veritabanı durumunda çalıştırıldı: `followup_1004.sql` uygulanmış halde ve temiz seed ile (41 kanıt). İkisinde de 55/55 geçti. pyflakes temiz. Sahte Gemini ile duman testi 4/4 PASS.

`test_tools` dosyasında istenen özel kontroller:

- Açık olaylar (1003, 1004, 1008) 8 olayın hiçbirinin benzerler listesinde çıkmıyor.
- Her olay için hedefin kendisi kendi sonucunda görünmüyor (8 olayın hepsi kontrol edildi).
- Dönen her olay veritabanında gerçekten `resolved` durumunda.
- INC-1004'ün 2 `data_gap` kaydı `evidence` listesinde yok, yalnızca `data_gaps` listesinde `GAP-` kimliği ve "MISSING DATA" notuyla görünüyor.
- `get_evidence` çıktısı, veritabanındaki alanlarla birebir aynı.
- `get_evidence` çıktısında hiçbir olay için `root_cause` geçmiyor.
- Bilinmeyen anahtar ya da enjeksiyon denemesi hata döndürüyor.

**Mina'nın bilgisayarında çalıştırıldı (2026-10-17):** `Ran 55 tests in 1.125s ... OK`. Gerçek MySQL 8.0.33 üzerinde, hiçbir test atlanmadı. Gerçek Gemini ile duman testi 4/4 `[PASS]`. Model `get_incident(INC-1003)` aracını tek adımda çağırdı.

## Dosyalar

| Dosya | Durum | Açıklama |
|---|---|---|
| `db/schema.sql` | mevcut | `incidents` ve `evidence` tabloları, CHECK kuralları, FK |
| `db/seed.sql` | mevcut, yorum düzeltildi | 8 olay, 41 kanıt. Bilgisayardaki kopyada başlık yorumu "40" diyordu, "41" olarak düzeltildi. Veri satırları aynı |
| `db/followup_1004.sql` | mevcut | INC-1004 için sonradan eklenecek pod dağılımı kanıtı. Tekrar çalıştırmak güvenli |
| `investigator/llm/base.py` | mevcut, değişmedi | Sağlayıcıdan bağımsız tipler |
| `investigator/llm/gemini.py` | mevcut, **küçük düzeltme** | Boş model turu artık açık bir hata veriyor (aşağıya bakın) |
| `investigator/llm/__init__.py` | mevcut, değişmedi | `LLM_PROVIDER`'a göre sağlayıcı seçimi |
| `investigator/db.py` | mevcut, değişmedi | PyMySQL, UTC oturumu, autocommit |
| `investigator/redact.py` | **yeni** | Hata mesajlarından API anahtarını ve DB şifresini siler |
| `scripts/smoke_test.py` | mevcut, **küçük düzeltme** | Hata mesajları redakte ediliyor. `get_incident` neden kök nedeni seçmediğini açıklayan yorum eklendi |
| `tests/fixtures/expected_outcomes.json` | **yeni** | Beklenen sonuçlar. Veritabanı ve `investigator/` paketi dışında |
| `tests/test_gemini_adapter.py` | **yeni** | 10 çevrimdışı test |
| `tests/test_smoke_loop.py` | **yeni** | 5 çevrimdışı test |
| `tests/test_security.py` | **yeni** | 4 çevrimdışı test |
| `tests/test_database.py` | **yeni** | 16 salt-okunur MySQL testi. DB'ye ulaşılamazsa otomatik atlanır |
| `docs/PROGRESS.md`, `docs/DECISIONS.md` | **yeni** | Bu dosyalar |
| `investigator/tools.py`, `tests/test_tools.py` | **yeni (3. adım)** | Ayrıntılar yukarıdaki "3. adım" bölümünde |
| `requirements.txt`, `.gitignore`, `.env.example` | mevcut, değişmedi | |
| `.env` | **dokunulmadı** | Yalnızca anahtar adları ve biçimi kontrol edildi, değerler okunmadı veya yazdırılmadı |

## Aşama 1 — Kod incelemesi sonuçları

| Kontrol | Sonuç |
|---|---|
| Python ve paket uyumu | ✅ Mina'nın ortamı: Python 3.14.6, google-genai 2.31.0, PyMySQL 1.2.3, python-dotenv 1.2.4. Testler aynı sürümlerle Python 3.14.6'da çalıştırıldı |
| `LLMProvider` sağlayıcıdan bağımsız mı? | ✅ Agent kodu yalnızca `base.py` tiplerini kullanıyor. Gemini'ye özgü tek alan opak `LLMResponse.raw`, yalnızca onu oluşturan sağlayıcı okuyor. Sınır: bir konuşmanın ortasında sağlayıcı değiştirilemez |
| Gemini tool call işleme | ✅ Mock testleriyle doğrulandı: araç çağrısı ve metin ayrılıyor, "thought" metni cevaba karışmıyor, otomatik araç çağırma kapalı |
| Call id + thought signature korunuyor mu? | ✅ Model turu değiştirilmeden geri gönderiliyor. `FunctionResponse.id` çağrının id'siyle eşleşiyor (test edildi) |
| Tekrarlama sınırlı mı? | ✅ En fazla 4 deneme (bekleme süreleri 10, 30, 60 s). Yalnızca 429 ve 5xx tekrarlanıyor, 4xx hemen hata veriyor |
| `db.py`: UTC ve parametreli sorgu | ✅ `SET time_zone='+00:00'` uygulanıyor. Sorgular `%s` parametresi kullanıyor, enjeksiyon denemesi düz metin olarak işlendi (test edildi) |
| Hata mesajlarında sır sızıntısı | 🟡→✅ Bilinen bir sızıntı yoktu, ama ham traceback'ler kontrolsüzdü. `redact()` eklendi ve duman testi artık kısa, redakte edilmiş hata yazıyor |
| `get_incident` kök nedeni sızdırıyor mu? | ✅ `root_cause` ve `root_cause_category` alanları seçilmiyor (açık ve çözülmüş olaylar için test edildi). Ek olarak veritabanındaki CHECK kuralı açık olaylara kök neden yazılmasını engelliyor |

### Yapılan düzeltmeler

1. **Boş model turu** (`gemini.py`): Model hem metin hem araç çağrısı içermeyen bir tur döndürürse (örneğin `MAX_TOKENS` ya da `SAFETY` nedeniyle), eskiden bu sessizce "cevap yok" olarak işleniyordu. Artık `finish_reason` bilgisiyle birlikte açık bir hata veriyor.
2. **Sır redaksiyonu** (`redact.py`, `smoke_test.py`): Beklenmeyen hatalar artık ham traceback yerine `[ERROR] <Tür>: <mesaj>` biçiminde yazılıyor ve mesajdaki sır değerleri siliniyor.

### Rapor edilen ama değiştirilmeyen noktalar

- Günlük kota dolduğunda dönen 429 hatası da tekrar deneniyor. Bu en fazla yaklaşık 100 saniye boşa beklemeye yol açar. Sınırlı olduğu için şimdilik bırakıldı.
- `get_incident` o tarihte `scripts/smoke_test.py` içindeydi. 3. adımda `investigator/tools.py` dosyasına taşındı.

## Aşama 2 ve 3 — SQL ve veritabanı

**Mina'nın MySQL'i (8.0.33):** Bu oturumdan erişilemiyor. Bulut ortamı kullanıcının localhost'una bağlanamıyor, `investigator` kullanıcısı da yalnızca `localhost` bağlantısına izinli. Bu yüzden oradaki veritabanında **hiçbir işlem yapılmadı**, hiçbir şey silinmedi ya da sıfırlanmadı. Önceki oturumda Mina Workbench'te şunları doğruladı: 8 olay, olay başına doğru kanıt sayıları, `investigator` kullanıcısı oluşturuldu ve `GRANT SELECT` verildi.

**Bulut test kopyası (MySQL 8.0.46):** Aynı SQL dosyalarıyla sıfırdan kuruldu ve şu kontroller yapıldı:

| Kontrol | Sonuç |
|---|---|
| `schema.sql` tabloları oluşturuyor mu? | ✅ |
| `seed.sql` verileri yüklüyor mu? | ✅ 8 olay, 41 kanıt (35 observation, 4 negative_finding, 2 data_gap) |
| FK: var olmayan olaya kanıt eklenebiliyor mu? | ✅ Hayır, hata 1452 |
| CHECK: `observed_until < observed_at` | ✅ Reddedildi |
| CHECK: `collected_at < observed_at` | ✅ Reddedildi |
| CHECK: açık olaya `root_cause` yazılabiliyor mu? | ✅ Hayır, reddedildi |
| CHECK: zaman aralığı olmayan `data_gap` | ✅ Reddedildi |
| CASCADE (ayrı bir karalama veritabanında) | ✅ INC-1007 silindi, 41 kanıttan 38'i kaldı. Karalama veritabanı sonra silindi |
| `followup_1004.sql` tekrar çalıştırılabilir mi? | ✅ İlk çalıştırmada 1 satır, ikincide 0 satır ekledi |
| Pencereli kanıtlarda `observed_until` dolu mu? | ✅ 6 negative_finding ve data_gap kaydının hepsinde dolu |
| Açık olaylarda kök neden boş mu? | ✅ INC-1003, 1004 ve 1008 |

**Kanıt sayısı hakkında not:** Görevde "yaklaşık 35" deniyordu, onaylanan tasarım 41 kanıt içeriyor. Fazladan kayıtlar, yanıltıcı geçmiş olayların (INC-1005 ve 1007) kendi kanıtları.

**`data_gap` kanıt olarak kullanılmamalı:** Veritabanı tarafındaki güvenceler test edildi. Her `data_gap` kaydının bir zaman aralığı var ve `limitations` alanı "bu aralık hakkında hiçbir şey söylemez" diyor. Hangi kayıtların hipotez kanıtı olarak gösterilmemesi gerektiği `expected_outcomes.json` dosyasında listelendi. **Agent seviyesindeki kontrol henüz yapılamaz**, çünkü hipotez çıktısı 4. aşamada oluşacak.

## Aşama 4 — Mock testleri

Komut: `python -m unittest discover -s tests -t . -v`

| Test grubu | Sayı | Gerçek sonuç (bulut, Python 3.14.6) |
|---|---|---|
| `test_gemini_adapter` | 10 | ✅ 10/10 |
| `test_smoke_loop` | 5 | ✅ 5/5 |
| `test_security` | 4 | ✅ 4/4 |
| `test_database` (`investigator` kullanıcısıyla) | 16 | ✅ 16/16 |
| **Toplam** | **35** | ✅ **35/35** |

Ek doğrulamalar:

- **Bilgisayardaki gerçek dosyalarla:** Dosyalar Mina'nın klasörüne yazıldıktan sonra, bilgisayardaki Linux çalışma ortamında (Python 3.14, aynı paket sürümleriyle) çevrimdışı testler çalıştırıldı: 19/19 geçti. `test_database` beklendiği gibi atlandı, çünkü bu ortam Windows'taki MySQL'e erişemiyor.
- **Negatif test:** Veritabanı testleri tam yetkili bir kullanıcıyla çalıştırıldığında `test_app_user_only_has_select_on_project_db` beklendiği gibi **başarısız oldu**. Yani bu test fazla yetkiyi gerçekten yakalıyor.
- **Önceki 4 duman kontrolü:** Sahte Gemini ve gerçek (bulut) MySQL ile tekrar çalıştırıldı, 4/4 PASS.

Kapsananlar: MySQL bağlantısı ve UTC, yalnızca SELECT yetkisi, `get_incident` doğru kaydı döndürüyor, kök neden sızmıyor (sorgu ve sonuç seviyesinde), araç çağrısı ile düz metin ayrımı ("aracı çağıracağım" demek PASS sayılmıyor), bilinmeyen araç, ulaşılamayan model, 4xx ve 429 davranışı, döngü üst sınırı (`MAX_STEPS = 3`), beklenen cevapların agent kodundan izole olması.

## Aşama 5 — Gerçek Gemini testi

### Mina'nın bilgisayarında çalıştırıldı (2026-10-17)

`python -m unittest discover -s tests -t . -v` → **`Ran 35 tests ... OK`**. 16 veritabanı testinin hepsi gerçek MySQL 8.0.33 üzerinde, `investigator` kullanıcısıyla çalıştı ve geçti. Hiçbir test atlanmadı.

Workbench'te `SHOW GRANTS FOR 'investigator'@'localhost'` sonucu: yalnızca `GRANT USAGE ON *.*` ve ``GRANT SELECT ON `evidence_gap`.*``. Beklenen yetkilerle aynı.

`python -m scripts.smoke_test`, gerçek `gemini-3.8-flash` ve gerçek MySQL 8.0.33 ile:

```
[PASS] model reachable
[PASS] database seeded - 8 incidents
  step 1: model called get_incident({'incident_key': 'INC-1003'})
[PASS] model called get_incident with the right key - 1 call(s)
[PASS] final answer contains the database title - expected 'checkout-api returning 502 errors'
```

Model aracı tek adımda gerçekten çağırdı ve cevabı veritabanından gelen başlığa dayandırdı. Bu testte 2 istek kullanıldı (`models.get` sayılmadan).

### Önceki oturumdan (bulut ve bilgisayardaki Linux ortamı)

- `.env` dosyasında `GEMINI_API_KEY` tanımlı. Biçim kontrolü yapıldı: 53 karakter, boşluk ve tırnak yok. Değeri hiçbir yerde gösterilmedi.
- Bu oturumun bilgisayardaki çalışma ortamı `generativelanguage.googleapis.com` adresine ulaşamıyor (`HTTP 403 from proxy`, alan adı izin listesinde değil). Anahtarı buluta kopyalamak sır güvenliğine aykırı olacağı için yapılmadı.
- Bu yüzden şunlar **doğrulanmadı**: modelin bu anahtarla erişilebilir olması, ücretsiz kota durumu, gerçek bir `get_incident` araç çağrısı.
- Hiçbir faturalandırma veya kota ayarına dokunulmadı.

## Hatalar (sırlar temizlenmiş)

| Ne | Mesaj |
|---|---|
| Gemini erişimi (oturum ortamı) | `curl: (56) Received HTTP code 403 from proxy after CONNECT` |
| İlk dosya yazma denemesi | `spawn E2BIG` (komut çok uzundu). Dosyalar başka bir yöntemle yazıldı, etkisi yok |

## Mina'nın bilgisayarında yapması gerekenler

cmd'de:

```
cd %USERPROFILE%\Desktop\agent
.venv\Scripts\activate
```

1. **Mock ve veritabanı testleri** (kota harcamaz):
   ```
   python -m unittest discover -s tests -t . -v
   ```
   Beklenen sonuç: `Ran 96 tests ... OK` (4. adımdan önce 55, 3. adımdan önce 35 testti). `test_database` veya `test_tools` atlandıysa ("skipped") MySQL'e bağlanılamamış demektir, mesajı paylaş.
2. **Yetki kontrolü** (Workbench):
   ```sql
   SHOW GRANTS FOR 'investigator'@'localhost';
   ```
   Yalnızca `GRANT USAGE ON *.*` ve ``GRANT SELECT ON `evidence_gap`.*`` satırları görünmeli.
3. **Gerçek Gemini duman testi** (yaklaşık 2–3 istek kullanır):
   ```
   python -m scripts.smoke_test
   ```
   Beklenen sonuç: 4 satır `[PASS]`. Kota durumunu https://aistudio.google.com/rate-limit adresinden görebilirsin. Billing açma.
4. `followup_1004.sql` dosyasını **henüz çalıştırma**. INC-1004'ün ilk gerçek araştırmasından sonra kullanılacak.
5. `python -m scripts.investigate ...` komutu **gerçek API kullanır**. Mina onay verene kadar çalıştırılmayacak.

## Sonraki aşama önerisi

1. Mina'nın bilgisayarında testler: `Ran 96 tests ... OK` bekleniyor.
2. Mina onay verirse gerçek API ile, tek tek:
   ```
   python -m scripts.investigate INC-1003
   python -m scripts.investigate INC-1004
   python -m scripts.investigate INC-1008
   python -m scripts.evaluate
   ```
   Beklenen kullanım yaklaşık 6–12 istek, üst sınır 24.
3. Sonra INC-1004 için `followup_1004.sql` dosyasını çalıştır ve olayı tekrar araştır (2–8 istek). Tek pod hipotezinin güçlenip güçlenmediğine bak.
4. Çıktı yapısı gerçek çalıştırmalarda kendini kanıtlarsa sonuç tablolarını (`investigations`, `hypotheses` …) tasarlayıp onaya sun.
