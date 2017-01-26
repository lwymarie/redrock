import numpy as np

import redrock.io
import time

class PlotSpec(object):
    def __init__(self, targets, templates, zscan, zfit, truth=None):
        '''
        TODO: document
        '''

        #- Isolate imports of optional dependencies
        import matplotlib.pyplot as plt

        self.targets = targets
        self.templates = templates
        self.zscan = zscan
        self.zfit = zfit
        self.itarget = 0
        self.znum = 0
        self.smooth = 1
        self.truth = truth
        
        self.fig = plt.figure()
        self.ax1 = self.fig.add_subplot(211)
        self.ax2 = self.fig.add_subplot(212)

        self.cid = self.fig.canvas.mpl_connect('key_press_event', self.onkeypress)

        #- Instructions
        print("-------------------------------------------------------------------------")
        print("Select window then use keyboard shortcuts to navigate:")
        print("    up/down arrow: previous/next target")
        print("    left/right arrow: previous/next redshift fit for this target")
        print("    (d)etails")
        print("-------------------------------------------------------------------------")

        #- Disable some default matplotlib key bindings so that we can use keys
        #- TODO: cache and reset when done
        plt.rcParams['keymap.forward'] = ''
        plt.rcParams['keymap.back'] = ''

        plt.ion()
        self.plot()
        plt.show()

    def onkeypress(self, event):
        ### print('key', event.key)
        if event.key == 'right':
            self.znum = (self.znum + 1) % self.nznum
            self.plot(keepzoom=True)
        elif event.key == 'left':
            self.znum = (self.znum - 1) % self.nznum
            self.plot(keepzoom=True)
        elif event.key == 'down':
            if self.itarget == len(self.targets)-1:
                print('At last target')
            else:
                self.znum = 0
                self.itarget += 1
                self.plot()
        elif event.key == 'up':
            if self.itarget == 0:
                print('Already at first target')
            else:
                self.znum = 0
                self.itarget -= 1
                self.plot()
        elif event.key == 'd':
            target = self.targets[self.itarget]    
            zfit = self.zfit[self.zfit['targetid'] == target.id]
            print('target {}'.format(target.id))
            print(zfit['znum', 'spectype', 'z', 'zerr', 'zwarn', 'chi2'])

    def plot(self, keepzoom=False):

        #- Isolate imports of optional dependencies
        from scipy.signal import medfilt
        import matplotlib.pyplot as plt
        
        target = self.targets[self.itarget]    
        zfit = self.zfit[self.zfit['targetid'] == target.id]
        self.nznum = len(zfit)
        zz = zfit[zfit['znum'] == self.znum][0]
        coeff = zz['coeff']

        for tp in self.templates:
            if tp.type == zz['spectype']:
                break
    
        if tp.type != zz['spectype']:
            raise ValueError('spectype {} not in templates'.format(zz['spectype']))

        #- zscan plot
        if keepzoom:
            force_xlim = self.ax1.get_xlim()
            force_ylim = self.ax1.get_ylim()

        self.ax1.clear()
        for spectype, fmt in [('STAR', 'k-'), ('GALAXY', 'b-'), ('QSO', 'g-')]:
            if spectype in self.zscan[target.id]:
                zx = self.zscan[target.id][spectype]
                self.ax1.plot(zx['redshifts'], zx['zchi2'], fmt, alpha=0.2, label='_none_')
                self.ax1.plot(zx['redshifts'], zx['zchi2']+zx['penalty'], fmt, label=spectype)

    
        self.ax1.plot(zfit['z'], zfit['chi2'], 'r.', label='_none_')
        for row in zfit:
            self.ax1.text(row['z'], row['chi2'], str(row['znum']), verticalalignment='top')

        if self.truth is not None:
            i = np.where(self.truth['targetid'] == target.id)[0]
            if len(i) > 0:
                ztrue = self.truth['ztrue'][i[0]]
                self.ax1.axvline(ztrue, color='g', alpha=0.5)
            else:
                print('WARNING: target id {} not in truth table'.format(target.id))

        self.ax1.axvline(zz['z'], color='k', alpha=0.1)
        self.ax1.axhline(zz['chi2'], color='k', alpha=0.1)
        self.ax1.legend()
        self.ax1.set_title('target {}  zbest={:.3f} {}'.format(target.id, zz['z'], zz['spectype']))
        self.ax1.set_ylabel(r'$\chi^2$')
        self.ax1.set_xlabel('redshift')
        if keepzoom:
            self.ax1.set_xlim(*force_xlim)
            self.ax1.set_ylim(*force_ylim)
    
        #- spectrum plot
        if keepzoom:
            force_xlim = self.ax2.get_xlim()
            force_ylim = self.ax2.get_ylim()
            
        self.ax2.clear()
        ymin = ymax = 0.0
        for spec in target.coadd:
            mx = tp.eval(coeff[0:tp.nbasis], spec.wave, zz['z']) * (1+zz['z'])
            model = spec.R.dot(mx)
            flux = spec.flux.copy()
            isbad = (spec.ivar == 0)
            ## model[isbad] = mx[isbad]
            flux[isbad] = np.NaN
            self.ax2.plot(spec.wave, medfilt(flux, self.smooth), alpha=0.5)
            self.ax2.plot(spec.wave, medfilt(mx, self.smooth), 'k:', alpha=0.8)
            model[isbad] = np.NaN
            self.ax2.plot(spec.wave, medfilt(model, self.smooth), 'k-', alpha=0.8)

            ymin = min(ymin, np.percentile(flux[~isbad], 1))
            ymax = max(ymax, np.percentile(flux[~isbad], 99), np.max(model)*1.05)

        #- Label object type and redshift
        label = 'znum {} {} z={:.3f}'.format(self.znum, zz['spectype'], zz['z'])
        print('target {} id {} {}'.format(self.itarget, target.id, label))
        ytext = ymin+0.9*(ymax-ymin)
        self.ax2.text(3800, ytext, label)

        #- ZWARN labels
        if zz['zwarn'] != 0:
            label = list()
            for name, mask in redrock.zwarning.ZWarningMask.flags():
                if (zz['zwarn'] & mask) != 0:
                    label.append(name)
            label = '\n'.join(label)
            color = 'r'
        else:
            label = 'ZWARN=0'
            color = 'g'

        self.ax2.text(10000, ytext, label, horizontalalignment='right', color=color)

        self.ax2.axhline(0, color='k', alpha=0.2)
        if keepzoom:
            self.ax2.set_xlim(*force_xlim)
            self.ax2.set_ylim(*force_ylim)
        else:
            self.ax2.set_ylim(ymin, ymax)
            self.ax2.set_xlim(3500,10100)

        self.ax2.set_ylabel('flux')
        self.ax2.set_xlabel('wavelength [A]')
        # self.fig.tight_layout()
        self.fig.canvas.draw()