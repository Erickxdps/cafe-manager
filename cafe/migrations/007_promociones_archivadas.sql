-- Una promoción que ya se vendió no se puede borrar (los pedidos la referencian): se archiva.
-- Archivada = no aparece en la lista ni en el selector de pedidos; el historial y los informes la conservan.
ALTER TABLE promotions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1));
