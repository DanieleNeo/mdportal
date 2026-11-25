# Mermaid — provenienza del bundle

`mermaid.bundle.js` è il contenuto del blocco `script#mermaid-lib` estratto dall'HTML fornito dall'utente in `sbe-next-docs (1).zip`. Non contiene i documenti del progetto. È mantenuto senza modifiche, con le informazioni di licenza già presenti in coda. Versione esatta di Mermaid non verificata. Mermaid upstream: https://github.com/mermaid-js/mermaid (MIT). Il bundle include altre librerie; i relativi avvisi originali sono conservati. Prima di una redistribuzione pubblica verificare e integrare le licenze complete delle dipendenze a partire dalla distribuzione originale.

## Decoder 4.0

- `brotli-decoder.js`: bundle dei moduli `dec` del pacchetto npm `brotli` 1.3.3 (https://github.com/devongovett/brotli.js), ricavati dalla distribuzione già installata nell'ambiente. Il package dichiara MIT; i file Google originari riportano copyright 2013 Google Inc. e licenza Apache-2.0, conservati integralmente nei moduli. Il bundle usa il dizionario browser compresso e `base64-js` con la sua licenza inclusa. Vedere `LICENSE-Apache-2.0` e `LICENSE-base64-js`. Prima di redistribuzione pubblica verificare anche la licenza MIT completa del pacchetto brotli.js upstream (non presente nella copia locale).
- `lzma-decoder.js`: implementazione JavaScript originale specifica per LZMA1 raw emesso dal generatore, senza dipendenze.
- Zstandard: nessun decoder incluso; l'utente può fornire un proprio bundle UMD fzstd verificato.


## MathJax 4.1

`mathjax-tex-svg.js`: distribuzione ufficiale `mathjax-full` 3.2.1, componente `es5/tex-svg-full.js` già disponibile nell'ambiente, non modificato. Upstream https://github.com/mathjax/MathJax ; licenza Apache-2.0 in `LICENSE-MathJax`.
