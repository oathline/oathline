BEGIN TRANSACTION;
CREATE TABLE proposals(id TEXT PRIMARY KEY, principal TEXT NOT NULL, capability TEXT NOT NULL, arguments TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, integrity TEXT NOT NULL, confirmed_by TEXT, items TEXT, items_hash TEXT);
INSERT INTO "proposals" VALUES('tok_f9739d38a620427eb1ec4c3b41891e33','agent','sms.send','{"body":"Hello from 0.2.0","to":"+61400000000"}','proposed',1700000000.0,1700000300.0,'f05df85c5121a975366d5809c7db8de3febbba826dcf045351691ba8a236442f',NULL,'[{"key":"to","said":"+61400000000","value":"+61400000000"},{"key":"body","said":"Hello from 0.2.0","value":"Hello from 0.2.0"}]','95100d09e9d2f8662d61d43e3136a0b5078b9bcb658d6601be574a95261de512');
INSERT INTO "proposals" VALUES('tok_5bb3f7761bde4e43be2ea7a4fc072f3a','agent','email.send','{"to":"bob@example.com"}','proposed',1700000000.0,1700000300.0,'19d476a50440e2014b6a3c98f88cd7e809aaba3ccccebd9feaa0c44e5d4dba5d',NULL,NULL,NULL);
COMMIT;
