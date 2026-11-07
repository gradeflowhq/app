// The Password page's recovery key card. Closing the dialog without saving keeps the previous key.

import { generateRecoveryKey, importRecoveryKey, rewrapPrivateKey } from "../lib/account-crypto.js";
import { loadWrappingKey, signedInAccount } from "../services/account.js";
import { send } from "../services/requests.js";
import { showToast } from "../services/toasts.js";

export const recoveryKeyCard = () => ({
  hasRecoveryKey: false,
  recoveryKey: "",
  copied: false,
  saving: false,

  init() {
    this.hasRecoveryKey = this.$root.dataset.hasRecoveryKey === "true";
  },
  get recoveryKeyGroups() {
    return this.recoveryKey.split("-");
  },

  create() {
    Object.assign(this, { recoveryKey: generateRecoveryKey(), copied: false });
    this.$refs.dialog.showModal();
  },
  async copy() {
    await navigator.clipboard.writeText(this.recoveryKey);
    this.copied = true;
  },
  async save() {
    this.saving = true;
    try {
      const recoveryWrappedPrivateKey = await rewrapPrivateKey(
        signedInAccount().wrappedPrivateKey,
        await loadWrappingKey(),
        await importRecoveryKey(this.recoveryKey),
      );
      await send("/settings/recovery-key/", { values: { recovery_wrapped_private_key: recoveryWrappedPrivateKey } });
      this.hasRecoveryKey = true;
      this.$refs.dialog.close();
      showToast("Recovery key saved.", "success");
    } catch {
      showToast("That didn't work. Try again in a moment.");
    } finally {
      this.saving = false;
    }
  },
  discard() {
    this.recoveryKey = "";
  },
});
