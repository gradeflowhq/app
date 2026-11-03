// A drop zone: whether a file is dragged over it, and `onFiles` (called as one of the component's methods) with the
// files dropped.
export const dropZone = (onFiles) => ({
  dragging: false,
  dragOver() {
    this.dragging = true;
  },
  dragLeave() {
    this.dragging = false;
  },
  drop(event) {
    this.dragging = false;
    onFiles.call(this, event.dataTransfer.files);
  },
});

// A file field shown as a drop zone (imports/drop.html) that sends its form, through any confirmation, once a file is
// dropped or chosen, and names the file so it can be sent again.
export const fileDrop = () => ({
  ...dropZone(function (files) {
    this.$refs.file.files = files;
    this.fileChosen();
  }),
  fileName: "",
  fileChosen() {
    this.fileName = this.$refs.file.files[0]?.name ?? "";
    if (this.fileName) this.$refs.submit.click();
  },
});
