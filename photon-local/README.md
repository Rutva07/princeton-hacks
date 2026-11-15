# Local Photon iMessage test

This standalone Python test uses the Photon 2.0 Beta REST API. It needs no
CausalForge server, database, or tunnel. It uses the official Photon Python SDK.

Run from this folder:

```sh
python3 -m unittest -v
python3 -m venv /tmp/causalforge-photon-sdk
/tmp/causalforge-photon-sdk/bin/python -m pip install photonhq-api==0.5.0
/tmp/causalforge-photon-sdk/bin/python smoke.py --check
/tmp/causalforge-photon-sdk/bin/python smoke.py
```

Use the project ID and key for your active iMessage line in the
[Photon Beta dashboard](https://photon.codes/beta). The script prompts for both
and hides the key, or reads `PHOTON_PROJECT_ID` and `PHOTON_API_KEY` from the environment.

Once the script says Ready, send **PING** to the Photon line from Messages.
Look for **PONG from CausalForge** in the same conversation. Use
`--sender +15555550100` with your own exact Photon handle to restrict replies.
Close any other Photon consumer using the same project during this test.
