# Mimari Kararlar

Her karar: ne seçildi, neden, bedeli.

## D1. Kanıt tarafsızdır, "destekler / çelişir" bilgisi ilişkide durur
`evidence` tablosu yalnızca gözlemi saklar. Bir kanıtın bir hipotezi destekleyip desteklemediği hipoteze göre değişir, bu yüzden bu bilgi ileride oluşturulacak `hypothesis_evidence` tablosunda tutulacak. Bedeli: kanıtlar arasındaki çelişki ayrı bir tabloda değil, aynı hipoteze zıt yönde bağlanan kanıtlar üzerinden görünür.

## D2. Üç kanıt türü: `observation`, `negative_finding`, `data_gap`
"Kayıt yok" bilgisi "olay olmadı" anlamına gelmez. `data_gap` hiçbir hipotezi desteklemez ya da çürütmez, yalnızca hangi ek bilginin gerektiğini gösterir. `negative_finding` bir hipotezi zayıflatabilir ama çürütmez.

## D3. Zaman aralıkları `observed_at` ve `observed_until` ile saklanır
"Son 24 saat" gibi göreli ifadeler metne yazılmaz. `negative_finding` ve `data_gap` kayıtları için aralık bitişi zorunludur (CHECK kuralı).

## D4. Tüm zamanlar UTC olarak `DATETIME(3)` tipinde
`TIMESTAMP` tipi oturumun saat dilimine göre sessizce dönüşüm yaptığı için kullanılmadı. Uygulama oturumu `time_zone='+00:00'` ile açar, arayüz zamanları yerel saate çevirir. Ayrıca iki ayrı zaman tutulur: gözlem zamanı (`observed_at`) ve kayıt zamanı (`collected_at`).

## D5. `limitations` ile `reliability` ayrı alanlar
`reliability` kaydın doğru olup olmadığını söyler. `limitations` ise kayıt doğru olsa bile neyi kanıtlayamayacağını söyler. Örneğin boş bir Redis slowlog'u `high` güvenilirliktedir, ama Redis'in sağlıklı olduğunu kanıtlamaz.

## D6. Cevap sızmasını önlemenin üç katmanı
1. Veritabanı: CHECK kuralı, çözülmemiş olaylara `root_cause` yazılmasını engeller.
2. Araçlar: `get_incident` kök nedeni hiçbir zaman seçmez. İleride yazılacak `find_similar_incidents` yalnızca başka ve çözülmüş olayların kök nedenini gösterecek.
3. Değerlendirme: Beklenen sonuçlar `tests/fixtures/expected_outcomes.json` dosyasında. Bu dosya veritabanının ve `investigator/` paketinin dışında, ve bir test paketin bu dosyaya hiç referans vermediğini kontrol ediyor.

## D7. `ON DELETE CASCADE` (yalnızca MVP için)
Artısı: Bir olay silinince kanıtları da silinir, sahipsiz kayıt kalmaz. Eksisi: Yanlışlıkla yapılan bir silme geri alınamaz. Sonuç tabloları kanıtlara referans vermeye başladığında `RESTRICT` ya da soft delete yöntemine geçilecek.

## D8. Agent veritabanına salt-okunur bir kullanıcıyla bağlanır
`investigator@localhost` kullanıcısının yalnızca ``SELECT ON `evidence_gap`.*`` yetkisi var. Root hiçbir dosyada saklanmıyor. Sonuç tabloları eklendiğinde yalnızca o tablolar için `INSERT` yetkisi verilecek. Bir test, kullanıcının fazladan yetkisi olup olmadığını kontrol ediyor.

## D9. Sağlayıcıdan bağımsız LLM katmanı
Agent yalnızca `investigator/llm/base.py` tiplerini kullanır. Gemini SDK'sını içe aktaran tek dosya `gemini.py`. OpenAI uyumlu endpoint yerine kendi küçük arayüzümüzü yazdık, çünkü bu hem Gemini'ye özgü özelliklere (thought signature) tam erişim sağlıyor hem de öğrenme amacına uygun. Bedeli: her yeni sağlayıcı için bir adaptör dosyası yazmak gerekiyor.

## D10. Model: `gemini-3.8-flash`, thinking seviyesi `low`
Google dokümanlarına göre stable, function calling destekli ve ücretsiz katmanı var. `minimal` seviyesi bu modelde hata verdiği için en düşük seviye `low`. Ücretsiz katmanda verilerin ürün geliştirmede kullanılması nedeniyle yalnızca sentetik veri kullanılıyor. Model ve thinking seviyesi `.env` üzerinden değiştirilebilir.

## D11. Otomatik araç çağırma kapalı, döngü bizde
Araçları SDK değil kendi kodumuz çalıştırır. Böylece her çağrı görülebilir ve test edilebilir, döngünün bir üst sınırı var (duman testinde `MAX_STEPS = 3`), ve "aracı çağıracağım" diyen düz bir metin gerçek bir araç çağrısı sayılmaz.

## D12. Sınırlı tekrar deneme
Yalnızca 429 ve 5xx hatalarında, toplam en fazla 4 deneme (bekleme süreleri 10, 30, 60 s). 4xx hataları hemen döner. Bilinen bedeli: günlük kota dolduğunda yaklaşık 100 saniye boşa bekleme.

## D13. MySQL sürücüsü: PyMySQL
Saf Python ile yazıldığı için her Python sürümünde sorunsuz kuruluyor. Oracle'ın `mysql-connector-python` paketinde 2026'da bazı Python sürümleri için kurulum dosyaları eksik yayınlanmıştı.

## D14. Testler `unittest` ile yazıldı
Ek bir paket gerektirmiyor. Veritabanı testleri salt-okunur ve MySQL'e ulaşılamazsa otomatik atlanıyor, çevrimdışı testler hiç API kotası kullanmıyor.

## D15. `data_gap` kayıtları araç çıktısında ayrı bir listede ve ayrı bir kimlikle döner
`get_evidence` kanıtları `evidence` (`E-<id>`), boşlukları `data_gaps` (`GAP-<id>`) altında döndürür. Model, bir boşluğu kanıt olarak gösterirse bu kimliğinden anlaşılır ve 4. aşamadaki doğrulama bunu reddedebilir. Bedeli: Zaman çizelgesi arayüzde gösterilirken iki listenin birleştirilmesi gerekecek.

## D16. Benzerlik: aynı servis veya anlamlı ortak başlık kelimesi
Embedding gibi bir yöntem yerine açıklanabilir, kural tabanlı bir eşleşme seçildi. Genel kelimeler ve servis adı kelime eşleşmesinde sayılmaz, her sonuç neden eşleştiğini söyler. Bedeli: Eşanlamlı kelimeleri yakalamaz (örneğin "5xx" ve "502"), yanıltıcı bir geçmiş olay daha üstte sıralanabilir. Hangi geçmiş olayın gerçekten uyduğuna kanıtlara bakarak agent karar verir.

## D17. Araç argümanları sıkı kontrol edilir
`execute_tool` yalnızca tek bir string `incident_key` argümanını kabul eder. Bilinmeyen araçlar ve fazladan argümanlar veritabanına gitmeden hata döndürür.

## D18. Sonuç bir araçla teslim edilir (`submit_investigation`)
Serbest metinden JSON ayıklamak yerine model sonucunu bu aracın argümanları olarak gönderir. Sağlayıcıdan bağımsızdır ve döngü bu araç çağrılınca biter. Şema modele yol gösterir, ama asıl güvence Python'daki doğrulamadır. Gemini'nin desteğinden emin olunmadığı için şemada `additionalProperties` kullanılmadı, beklenmeyen alanları doğrulama reddediyor.

## D19. Model çıktısına güvenilmez, katı doğrulama ve tek düzeltme hakkı
Ayrıntılar PROGRESS.md dosyasının 4. adım bölümünde. Geçersiz bir sonuç hiçbir zaman geçerliymiş gibi kaydedilmez. Reddedilen denemeler etiketlenerek ve ihlalleriyle birlikte saklanır.

## D20. Doğrulama ile değerlendirme ayrıdır
Doğrulama (`validation.py`) her çalıştırmada uygulanan, cevabı bilmeyen kurallardır. Değerlendirme (`scripts/evaluate.py`) cevabı bilir ve yalnızca test amacıyla çalışır. Agent kodu fixture dosyasını hiçbir zaman okumaz, bunu bir test kontrol ediyor. Örnek: Redis'in tek bir bellek ölçümüyle elenmesi kurallara uygundur (çelişen kanıt bir observation), ama yanlıştır. Bu yüzden onu değerlendirme yakalar, doğrulama yakalamaz.

## D21. Sınırlar: tur sayısı API isteğinin üst sınırıdır
Bir model turu bir istektir. Toplam 8 tur sınırına düzeltme turu da dahildir. Son turda yalnızca teslim aracı sunulur. Araç çağrıları tur başına 6, toplamda 20 ile sınırlı, tekrarlanan çağrılar önbellekten cevaplanır.
