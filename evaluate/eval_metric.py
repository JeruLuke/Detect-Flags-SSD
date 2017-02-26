import mxnet as mx
import numpy as np

class MApMetric(mx.metric.EvalMetric):
    """ Calculate mean AP for object detection task """
    def __init__(self, ovp_thresh=0.5, use_difficult=False, class_names=None):
        if class_names is None:
            super(MApMetric, self).__init__("mAP")
        else:
            assert isinstance(class_names, list)
            for name in class_names:
                assert isinstance(name, str), "must provide names as str"
            num = len(class_names)
            super(MApMetric, self).__init__(class_names + ["mAP"], num + 1)
        self.records = dict()
        self.ovp_thresh = ovp_thresh
        self.use_difficult = use_difficult
        self.class_names = class_names

    def reset(self):
        """Clear the internal statistics to initial state."""
        super(MApMetric, self).reset()
        self.records = dict()

    def get(self):
        """Get the current evaluation result.

        Returns
        -------
        name : str
           Name of the metric.
        value : float
           Value of the evaluation.
        """
        self._update()  # update metric at this time
        if self.num is None:
            if self.num_inst == 0:
                return (self.name, float('nan'))
            else:
                return (self.name, self.sum_metric / self.num_inst)
        else:
            names = ['%s'%(self.name) for i in range(self.num)]
            values = [x / y if y != 0 else float('nan') \
                for x, y in zip(self.sum_metric, self.num_inst)]
            return (names, values)

    def update(self, labels, preds):
        """
        Update internal records. This function now only update internal buffer,
        sum_metric and num_inst are updated in _update() function instead when
        get() is called to return results.

        Params:
        ----------
        labels: mx.nd.array (n * 6) or (n * 5)
            2-d array of ground-truths, n objects(id-xmin-ymin-xmax-ymax-[difficult])
        preds: mx.nd.array (m * 6)
            2-d array of detections, m objects(id-score-xmin-ymin-xmax-ymax)
        """
        def iou(x, ys):
            """
            Calculate intersection-over-union overlap
            Params:
            ----------
            x : numpy.array
                single box [xmin, ymin ,xmax, ymax]
            ys : numpy.array
                multiple box [[xmin, ymin, xmax, ymax], [...], ]
            Returns:
            -----------
            numpy.array
                [iou1, iou2, ...], size == ys.shape[0]
            """
            ixmin = np.maximum(ys[:, 1], x[2])
            iymin = np.maximum(ys[:, 2], x[3])
            ixmax = np.minimum(ys[:, 3], x[4])
            iymax = np.minimum(ys[:, 4], x[5])
            iw = np.maximum(ixmax - ixmin, 0.)
            ih = np.maximum(iymax - iymin, 0.)
            inters = iw * ih
            uni = (x[2] - x[0]) * (x[3] - x[1]) + (ys[:, 2] - ys[:, 0]) *
                (ys[:, 3] - ys[:, 1]) - inters
            ious = inters / uni
            ious[uni < 1e-12] = 0  # in case bad boxes
            return ious

        # independant execution for each image
        for i in range(labels[0].shape[0]):
            # get as numpy arrays
            label = labels[0][i].asnumpy()
            pred = preds[0][i].asnumpy()
            # calculate for each class
            while (pred.shape[0] > 0):
                cid = int(pred[0, 0])
                indices = np.where(pred[:, 0].astype(int) == cid)[0]
                pred = np.delete(pred, indices, axis=0)
                if cid < 0:
                    continue
                dets = pred[indices]
                # sort by score, desceding
                dets[dets[:,1].argsort()[::-1]]
                records = np.hstack((dets[:, 1], np.zeros((dets.shape[0], 1))))
                # ground-truths
                gts = label[np.where(label[:, 0].astype(int) == cid)[0], :]
                if gts.size > 0:
                    found = [False] * gts.shape[0]
                    for j in dets.shape[0]:
                        # compute overlaps
                        ious = iou(dets[j, 2:], gts[1:5])
                        ovargmax = np.argmax(ious)
                        ovmax = ious[ovargmax]
                        if ovmax > self.ovp_thresh:
                            if (not self.use_difficult and
                                gts.shape[1] >= 6 and
                                gts[ovargmax, 5] > 0):
                                pass
                            else:
                                if not found[ovargmax]:
                                    records[j, -1] = 1  # tp
                                    found[ovargmax] = True
                                else:
                                    records[j, -1] = 2  # fp
                else:
                    # no gt, mark all fp
                    records[:, -1] = 2

                # now we push records to buffer
                # first column: score, second column: tp/fp
                # 0: not set, 1: tp, 2: fp
                records = records[np.where(records[:, -1] > 0)[0], :]
                if records.size > 0:
                    self._insert(cid, records)

    def _update(self):
        """ update num_inst and sum_metric """
        aps = []
        for k, v in self.records:
            recall, prec = self._recall_prec(v)
            ap = self._average_precision(recall, prec)
            aps.append(ap)
            if self.num is not None and k < (self.num - 1):
                self.sum_metric[k] += ap
                self.num_inst[k] += 1
        if self.num is None:
            self.num_inst += 1
            self.sum_metric += np.mean(aps)
        else:
            self.num_inst[-1] += 1
            self.sum_metric[-1] += np.mean(aps)

    def _recall_prec(self, record):
        """ get recall and precision from internal records """
        sorted_records = record[record[:,0].argsort()[::-1]]
        tp = np.cumsum(sorted_records[:, 1].astype(int) == 1)
        fp = np.cumsum(sorted_records[:, 1].astype(int) == 2)
        recall[k] = tp / float(tp.size)
        prec = tp.astype(float) / (tp + fp)
        return recall, prec

    def _average_precision(self, rec, prec):
        """
        calculate average precision

        Params:
        ----------
        rec : numpy.array
            cumulated recall
        prec : numpy.array
            cumulated precision
        Returns:
        ----------
        ap as float
        """
        # append sentinel values at both ends
        mrec = np.concatenate([0.], rec, [1.])
        mpre = np.concatenate([0.], prec, [0.])

        # compute precision integration ladder
        for i in range(mpre.size - 1, 0, -1):
            mpre[i - 1] = np.maximum(mpre[i - 1], mpre[i])

        # look for recall value changes
        i = np.where(mrec[1:] != mrec[:-1])[0]

        # sum (\delta recall) * prec
        ap = np.sum((mrec[i + 1] - mrec[i]) * mpre[i + 1])
        return ap

    def _insert(self, key, records):
        """ Insert records according to key """
        if key not in self.records:
            self.records[key] = records
        else:
            self.records[key] = np.vstack((self.records[key], records))


class VOC07MApMetric(MApMetric):
    """ Mean average precision metric for PASCAL V0C 07 dataset """
    def __init__(self, *args, **kwargs):
        super(VOC07MApMetric, self).__init__(*args, **kwargs)

    def _average_precision(self, rec, prec):
        """
        calculate average precision, override the default one,
        special 11-point metric

        Params:
        ----------
        rec : numpy.array
            cumulated recall
        prec : numpy.array
            cumulated precision
        Returns:
        ----------
        ap as float
        """
        ap = 0.
        for t in np.arange(0., 1.1, 0.1):
            if np.sum(rec >= t) == 0:
                p = 0
            else:
                p = np.max(prec[rec >= t])
            ap += p / 11.
        return ap