// The marks axis of a histogram of totals.

/** The axis' bounds: 0 to the maximum marks, with half a mark to spare either side when each bar is one mark. */
export const bounds = ([[start, end]], maxMarks) => (start === end ? [-0.5, maxMarks + 0.5] : [0, maxMarks]);

/** The round ticks from 0, ending at the maximum marks in place of any round tick too close to it. */
export const ticks = (values, maxMarks) => {
  const step = values.length > 1 ? values[1] - values[0] : maxMarks;
  return [...values.filter((value) => value >= 0 && maxMarks - value >= step / 2), maxMarks];
};
