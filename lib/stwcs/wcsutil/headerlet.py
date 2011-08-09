from __future__ import division
import logging
import os
import tarfile
import tempfile
import time
import warnings
from cStringIO import StringIO

import numpy as np
import pyfits

import altwcs
import wcscorr
from hstwcs import HSTWCS
from mappings import basic_wcs
from stsci.tools.fileutil import countExtn

module_logger = logging.getLogger('headerlet')

import atexit
atexit.register(logging.shutdown)

def setLogger(logger, level, mode='w'):
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    log_filename = 'headerlet.log'
    fh = logging.FileHandler(log_filename, mode=mode)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(level)

def isWCSIdentical(scifile, file2, verbose=False):
    """
    Compares the WCS solution of 2 files.

    Parameters
    ----------
    scifile: file1
    file2: file2
    verbose: False or a python logging level
             (one of 'INFO', 'DEBUG' logging levels)
             (an integer representing a logging level)

    Notes
    -----
    These can be 2 science observations or 2 headerlets
    or a science observation and a headerlet. The two files
    have the same WCS solution if the following are the same:

    - rootname/destim
    - primary WCS
    - SIP coefficients
    - NPOL distortion
    - D2IM correction
    - Velocity aberation

    """
    if verbose:
        setLogger(module_logger, verbose)
    else:
        module_logger.setLevel(100)

    module_logger.info("Starting isWCSIdentical: %s" % time.asctime())

    result = True
    numsci1 = max(countExtn(scifile), countExtn(scifile, 'SIPWCS'))
    numsci2 = max(countExtn(file2), countExtn(file2, 'SIPWCS'))

    if numsci1 == 0 or numsci2 == 0 or numsci1 != numsci2:
        module_logger.info("Number of SCI and SIPWCS extensions do not match.")
        result = False

    if getRootname(scifile) != getRootname(file2):
        module_logger.info('Rootnames do not match.')
        result = False
    try:
        extname1 = pyfits.getval(scifile, 'EXTNAME', ext=('SCI', 1))
    except KeyError:
        extname1 = 'SIPWCS'
    try:
        extname2 = pyfits.getval(file2, 'EXTNAME', ext=('SCI', 1))
    except KeyError:
        extname2 = 'SIPWCS'

    for i in range(1, numsci1 + 1):
        w1 = HSTWCS(scifile, ext=(extname1, i))
        w2 = HSTWCS(file2, ext=(extname2, i))
        if not np.allclose(w1.wcs.crval, w2.wcs.crval, rtol=1e-7) or \
        not np.allclose(w1.wcs.crpix, w2.wcs.crpix, rtol=1e-7)  or \
        not np.allclose(w1.wcs.cd, w2.wcs.cd, rtol=1e-7) or \
        not (np.array(w1.wcs.ctype) == np.array(w2.wcs.ctype)).all():
            module_logger.info('Primary WCSs do not match')
            result = False
        if w1.sip or w2.sip:
            if (w2.sip and not w1.sip) or (w1.sip and not w2.sip) or \
               not np.allclose(w1.sip.a, w2.sip.a, rtol=1e-7) or \
               not np.allclose(w1.sip.b, w2.sip.b, rtol=1e-7):
                module_logger.info('SIP coefficients do not match')
                result = False
        if w1.cpdis1 or w2.cpdis1:
            if w1.cpdis1 and not w2.cpdis1 or \
                w2.cpdis1 and not w1.cpdis1 or \
                not np.allclose(w1.cpdis1.data, w2.cpdis1.data):
                module_logger.info('NPOL distortions do not match')
                result = False
        if w1.cpdis2 or w2.cpdis2:
            if w1.cpdis2 and not w2.cpdis2 or \
                w2.cpdis2 and not w1.cpdis2 or \
                not np.allclose(w1.cpdis2.data, w2.cpdis2.data):
                module_logger.info('NPOL distortions do not match')
                result = False
        if w1.det2im1 or w2.det2im1:
            if w1.det2im1 and not w2.det2im1 or \
                w2.det2im1 and not w1.det2im1 or\
                not np.allclose(w1.det2im1.data, w2.det2im1.data):
                module_logger.info('Det2Im corrections do not match')
                result =  False
        if w1.det2im2 or w2.det2im2:
            if w1.det2im2 and not w2.det2im2 or \
                w2.det2im2 and not w1.det2im2 or\
                not np.allclose(w1.det2im2.data, w2.det2im2.data):
                module_logger.info('Det2Im corrections do not match')
                result = False
        if w1.vafactor != w2.vafactor:
            module_logger.info('VA factors do not match')
            result = False

    return result


# TODO: It would be logical for this to be part of the Headerlet class, perhaps
# as a classmethod
def createHeaderlet(fname, hdrname, destim=None, output=None, verbose=False, logmode='w'):
    """
    Create a headerlet from a science observation

    Parameters
    ----------
    fname: string
           Name of file with science observation
    hdrname: string
           Name for the headerlet, stored in the primary header of the headerlet
    destim: string
           Destination image, stored in the primary header of the headerlet.
           If None ROOTNAME is used of the science observation is used.
           ROOTNAME has precedence, destim is used for observations without
           ROOTNAME in the primary header
    output: string
           Save the headerlet to the given filename.
    verbose: False or a python logging level
             (one of 'INFO', 'DEBUG' logging levels)
             (an integer representing a logging level)
    logmode: 'w', 'a'
            used internally for controling access to the log file
    """

    if verbose:
        setLogger(module_logger, verbose, mode=logmode)
    else:
        module_logger.setLevel(100)

    module_logger.info("Starting createHeaderlet: %s" % time.asctime())
    phdukw = {'IDCTAB': True,
            'NPOLFILE': True,
            'D2IMFILE': True}
    if not isinstance(fname, pyfits.HDUList):
        fobj = pyfits.open(fname)
        close_file = True
    else:
        fobj = fname
        close_file = False
    if destim is None:
        try:
            destim = fobj[0].header['ROOTNAME']
        except KeyError:
            module_logger.exception('Required keyword "DESTIM" not found')
            print 'Please provide a value for the DESTIM keyword'
            raise
    if hdrname is None:
        module_logger.critical("Required keyword 'HDRNAME' not given")
        raise ValueError("Please provide a name for the headerlet, HDRNAME is "
                         "a required parameter.")

    # get the version of STWCS used to create the WCS of the science file.
    try:
        upwcsver = fobj[0].header.ascard['STWCSVER']
    except KeyError:
        upwcsver = pyfits.Card("STWCSVER", " ",
                               "Version of STWCS used to update the WCS")

    # get all keys for alternate WCSs
    altkeys = altwcs.wcskeys(fobj[('SCI', 1)].header)

    if 'O' in altkeys:
        altkeys.remove('O')

    numsci = countExtn(fname, 'SCI')
    module_logger.debug("Number of 'SCI' extensions in file %s is %s"
                 % (fname, numsci))
    hdul = pyfits.HDUList()
    phdu = pyfits.PrimaryHDU()
    phdu.header.update('DESTIM', destim,
                       comment='Destination observation root name')
    phdu.header.update('HDRNAME', hdrname, comment='Headerlet name')
    fmt="%Y-%m-%dT%H:%M:%S"
    phdu.header.update('DATE', time.strftime(fmt),
                       comment='Date FITS file was generated')
    phdu.header.ascard.append(upwcsver)

    refs = updateRefFiles(fobj[0].header.ascard, phdu.header.ascard,
                          verbose=verbose)
    phdukw.update(refs)
    phdu.header.update(key='VAFACTOR',
                       value=fobj[('SCI',1)].header.get('VAFACTOR', 1.))
    hdul.append(phdu)

    for e in range(1, numsci + 1):
        hwcs = HSTWCS(fname, ext=('SCI', e))
        h = hwcs.wcs2header(sip2hdr=True).ascard
        for ak in altkeys:
            awcs = HSTWCS(fname,ext=('SCI', e), wcskey=ak)
            h.extend(awcs.wcs2header(idc2hdr=False).ascard)
        h.append(pyfits.Card(key='VAFACTOR', value=hwcs.vafactor,
                             comment='Velocity aberration plate scale factor'))
        h.insert(0, pyfits.Card(key='EXTNAME', value='SIPWCS',
                                comment='Extension name'))
        h.insert(1, pyfits.Card(key='EXTVER', value=e,
                                comment='Extension version'))

        fhdr = fobj[('SCI', e)].header.ascard
        if phdukw['NPOLFILE']:
            cpdis = fhdr['CPDIS*...']
            for c in range(1, len(cpdis) + 1):
                h.append(cpdis[c - 1])
                dp = fhdr['DP%s.*...' % c]
                h.extend(dp)

                try:
                    h.append(fhdr['CPERROR%s' % c])
                except KeyError:
                    pass

            try:
                h.append(fhdr['NPOLEXT'])
            except KeyError:
                pass

        if phdukw['D2IMFILE']:
            try:
                h.append(fhdr['D2IMEXT'])
            except KeyError:
                pass

            try:
                h.append(fhdr['AXISCORR'])
            except KeyError:
                module_logger.exception("'D2IMFILE' kw exists but keyword 'AXISCORR' was not found in "
                                 "%s['SCI',%d]" % (fname, e))
                raise

            try:
                h.append(fhdr['D2IMERR'])
            except KeyError:
                h.append(pyfits.Card(key='DPERROR', value=0,
                                     comment='Maximum error of D2IMARR'))

        hdu = pyfits.ImageHDU(header=pyfits.Header(h))
        hdul.append(hdu)
    numwdvarr = countExtn(fname, 'WCSDVARR')
    numd2im = countExtn(fname, 'D2IMARR')
    for w in range(1, numwdvarr + 1):
        hdu = fobj[('WCSDVARR', w)].copy()
        hdul.append(hdu)
    for d in range(1, numd2im + 1):
        hdu = fobj[('D2IMARR', d)].copy()
        hdul.append(hdu)
    if output is not None:
        # write the headerlet to a file
        if not output.endswith('_hdr.fits'):
            output = output + '_hdr.fits'
        hdul.writeto(output, clobber=True)
    if close_file:
        fobj.close()
    return Headerlet(hdul,verbose=verbose, logmode='a')

def applyHeaderlet(hdrfile, destfile, createheaderlet=True, hdrname=None,
                   verbose=False):
    """
    Apply headerlet 'hdrfile' to a science observation 'destfile'

    Parameters
    ----------
    hdrfile: string
             Headerlet file
    destfile: string
             File name of science observation whose WCS solution will be updated
    createheaderlet: boolean
            True (default): before updating, create a headerlet with the
            WCS old solution.
    hdrname: string or None (default)
            will be the value of the HDRNAME keyword in the headerlet generated
            for the old WCS solution.  If not specified, a sensible default
            will be used.  Not required if createheaderlet is False
    verbose: False or a python logging level
             (one of 'INFO', 'DEBUG' logging levels)
             (an integer representing a logging level)
    """
    if verbose:
        setLogger(module_logger, verbose)
    else:
        module_logger.setLevel(100)
    module_logger.info("Starting applyHeaderlet: %s" % time.asctime())
    hlet = Headerlet(hdrfile, verbose=verbose, logmode='a')
    hlet.apply(destfile, createheaderlet=createheaderlet, hdrname=hdrname)

def updateRefFiles(source, dest, verbose=False):
    """
    Update the reference files name in the primary header of 'dest'
    using values from 'source'

    Parameters
    ----------
    source: pyfits.Header.ascardlist
    dest:   pyfits.Header.ascardlist
    """
    module_logger.info("Updating reference files")
    phdukw = {'IDCTAB': True,
            'NPOLFILE': True,
            'D2IMFILE': True}

    try:
        wind = dest.index_of('HISTORY')
    except KeyError:
        wind = len(dest)
    for key in phdukw.keys():
        try:
            value = source[key]
            dest.insert(wind, value)
        except KeyError:
            # TODO: I don't understand what the point of this is.  Is it meant
            # for logging purposes?  Right now it isn't used.
            phdukw[key] = False
    return phdukw

def getRootname(fname):
    """
    returns the value of ROOTNAME or DESTIM
    """

    try:
        rootname = pyfits.getval(fname, 'ROOTNAME')
    except KeyError:
        rootname = pyfits.getval(fname, 'DESTIM')
    return rootname

def mapFitsExt2HDUListInd(fname, extname):
    """
    Map FITS extensions with 'EXTNAME' to HDUList indexes.
    """

    if not isinstance(fname, pyfits.HDUList):
        f = pyfits.open(fname)
        close_file = True
    else:
        f = fname
        close_file = False
    d = {}
    for hdu in f:
        if 'EXTNAME' in hdu.header and hdu.header['EXTNAME'] == extname:
            extver = hdu.header['EXTVER']
            d[(extname, extver)] = f.index_of((extname, extver))
    if close_file:
        f.close()
    return d


class Headerlet(pyfits.HDUList):
    """
    A Headerlet class
    Ref: http://stsdas.stsci.edu/stsci_python_sphinxdocs/stwcs/headerlet_def.html
    """

    def __init__(self, fobj, wcskeys=[], mode='copyonwrite', verbose=False,
                 logmode='w'):
        """
        Parameters
        ----------
        fobj:  string
                Name of headerlet file, file-like object, a list of HDU
                instances, or an HDUList instance
        wcskeys: python list
                a list of wcskeys to be included in the headerlet
                created from the old WCS solution before the
                science file is updated. If empty: all alternate (if any)
                WCSs are copied to the headerlet.
        mode: string, optional
                Mode with which to open the given file object
        """
        self.verbose = verbose
        self.hdr_logger = logging.getLogger('headerlet.Headerlet')
        if self.verbose:
                setLogger(self.hdr_logger, self.verbose, mode=logmode)
        else:
            self.hdr_logger.setLevel(100)

        self.hdr_logger.info("Creating a Headerlet object from wcskeys %s" % wcskeys)
        self.wcskeys = wcskeys
        if not isinstance(fobj, list):
            fobj = pyfits.open(fobj, mode=mode)

        super(Headerlet, self).__init__(fobj)
        self.fname = self.filename()
        self.hdrname = self[0].header["HDRNAME"]
        self.stwcsver = self[0].header.get("STWCSVER", "")
        self.destim = self[0].header["DESTIM"]
        self.idctab = self[0].header.get("IDCTAB", "")
        self.npolfile = self[0].header.get("NPOLFILE", "")
        self.d2imfile = self[0].header.get("D2IMFILE", "")
        self.vafactor = self[1].header.get("VAFACTOR", 1) #None instead of 1?
        self.d2imerr = 0
        self.axiscorr = 1

    def apply(self, dest, createheaderlet=True, hdrname=None, attach=True,
              createsummary=True):
        """
        Apply this headerlet to a file.

        Parameters
        ----------
        dest:    string
                 Name of file to which to apply the WCS in the headerlet
        hdrname: string
                 A unique name for the headerlet
        createheaderlet: boolean
                 A flag which indicates if a headerlet should be created
                 from the old WCS and attached to the science file (default: True)
        attach:  boolean, default: True
                 By default the headerlet being applied will be attached
                 as an extension to the science file. Set 'attach' to False
                 to disable this.
        createsummary: boolean, default: True
                 Set this to False to disable creating and updating of wcscorr table.
                 This is used primarily for testing.
        """
        self.hverify()
        if self.verify_dest(dest):
            if not isinstance(dest, pyfits.HDUList):
                fobj = pyfits.open(dest, mode='update')
                close_dest = True
            else:
                fobj = dest
                close_dest = False

            # Create the WCSCORR HDU/table from the existing WCS keywords if
            # necessary
            if createsummary:
                try:
                    # TODO: in the pyfits refactoring branch if will be easier to
                    # test whether an HDUList contains a certain extension HDU
                    # without relying on try/except
                    wcscorr_table = fobj['WCSCORR']
                except KeyError:
                    # The WCSCORR table needs to be created
                    wcscorr.init_wcscorr(fobj)

            orig_hlt_hdu = None
            numhlt = countExtn(fobj, 'HDRLET')
            if createheaderlet:
                # Create a headerlet for the original WCS data in the file,
                # create an HDU from the original headerlet, and append it to
                # the file
                if not hdrname:
                    hdrname = fobj[0].header['ROOTNAME'] + '_orig'
                orig_hlt = createHeaderlet(fobj, hdrname, verbose=self.verbose, logmode='a')
                orig_hlt_hdu = HeaderletHDU.fromheaderlet(orig_hlt)
                orig_hlt_hdu.update_ext_version(numhlt + 1)
                numhlt += 1

            self._delDestWCS(fobj)
            refs = updateRefFiles(self[0].header.ascard, fobj[0].header.ascard, verbose=self.verbose)
            numsip = countExtn(self, 'SIPWCS')
            for idx in range(1, numsip + 1):
                fhdr = fobj[('SCI', idx)].header.ascard
                siphdr = self[('SIPWCS', idx)].header.ascard
                # a minimal attempt to get the position of the WCS keywords group
                # in the header by looking for the PA_APER kw.
                # at least make sure the WCS kw are written befir the HISTORY kw
                # if everything fails, append the kw to the header
                try:
                    wind = fhdr.index_of('PA_APER')
                except KeyError:
                    try:
                        wind = fhdr.index_of('HISTORY')
                    except KeyError:
                        wind = len(fhdr)
                self.hdr_logger.debug("Inserting WCS keywords at index %s" % wind)
                for k in siphdr:
                    if k.key not in ['XTENSION', 'BITPIX', 'NAXIS', 'PCOUNT',
                                     'GCOUNT','EXTNAME', 'EXTVER', 'ORIGIN',
                                     'INHERIT', 'DATE', 'IRAF-TLM']:
                        fhdr.insert(wind, k)
                    else:
                        pass

            #! Always attach these extensions last. Otherwise their headers may
            # get updated with the other WCS kw.
            numwdvar = countExtn(self, 'WCSDVARR')
            numd2im = countExtn(self, 'D2IMARR')
            for idx in range(1, numwdvar + 1):
                fobj.append(self[('WCSDVARR', idx)].copy())
            for idx in range(1, numd2im + 1):
                fobj.append(self[('D2IMARR', idx)].copy())

            # Update the WCSCORR table with new rows from the headerlet's WCSs
            if createsummary:
                wcscorr.update_wcscorr(fobj, self, 'SIPWCS')

            # Append the original headerlet
            if createheaderlet and orig_hlt_hdu:
                fobj.append(orig_hlt_hdu)

            if attach:
                # Finally, append an HDU for this headerlet
                new_hlt = HeaderletHDU.fromheaderlet(self)
                new_hlt.update_ext_version(numhlt + 1)
                fobj.append(new_hlt)

            if close_dest:
                fobj.close()
        else:
            self.hdr_logger.critical("Observation %s cannot be updated with headerlet "
                            "%s" % (fobj.filename(), self.hdrname))
            print "Observation %s cannot be updated with headerlet %s" \
                  % (fobj.filename(), self.hdrname)


    def hverify(self):
        self.verify()
        header = self[0].header
        assert('DESTIM' in header and header['DESTIM'].strip())
        assert('HDRNAME' in header and header['HDRNAME'].strip())
        assert('STWCSVER' in header)


    def verify_dest(self, dest):
        """
        verifies that the headerlet can be applied to the observation

        DESTIM in the primary header of the headerlet must match ROOTNAME
        of the science file (or the name of the destination file)
        """

        try:
            if not isinstance(dest, pyfits.HDUList):
                droot = pyfits.getval(dest, 'ROOTNAME')
            else:
                droot = dest[0].header['ROOTNAME']
        except KeyError:
            self.hdr_logger.debug("Keyword 'ROOTNAME' not found in destination file")
            droot = dest.split('.fits')[0]
        if droot == self.destim:
            self.hdr_logger.debug("verify_destim() returned True")
            return True
        else:
            self.hdr_logger.debug("verify_destim() returned False")
            return False

    def tofile(self, fname, destim=None, hdrname=None, clobber=False):
        if not destim or not hdrname:
            self.hverify()
        self.writeto(fname, clobber=clobber)

    def _delDestWCS(self, dest):
        """
        Delete the WCS of a science file
        """

        self.hdr_logger.info("Deleting all WCSs of file %s" % dest.filename())
        numext = len(dest)

        for idx in range(numext):
            # Only delete WCS from extensions which may have WCS keywords
            if ('XTENSION' in dest[idx].header and
                dest[idx].header['XTENSION'] == 'IMAGE'):
                self._removeD2IM(dest[idx])
                self._removeSIP(dest[idx])
                self._removeLUT(dest[idx])
                self._removePrimaryWCS(dest[idx])
                self._removeIDCCoeffs(dest[idx])
                try:
                    del dest[idx].header.ascard['VAFACTOR']
                except KeyError:
                    pass

        self._removeRefFiles(dest[0])
        self._removeAltWCS(dest, ext=range(numext))
        numwdvarr = countExtn(dest, 'WCSDVARR')
        numd2im = countExtn(dest, 'D2IMARR')
        for idx in range(1, numwdvarr + 1):
            del dest[('WCSDVARR', idx)]
        for idx in range(1, numd2im + 1):
            del dest[('D2IMARR', idx)]

    def _removeRefFiles(self, phdu):
        """
        phdu: Primary HDU
        """
        refkw = ['IDCTAB', 'NPOLFILE', 'D2IMFILE']
        for kw in refkw:
            try:
                del phdu.header.ascard[kw]
            except KeyError:
                pass

    def _removeSIP(self, ext):
        """
        Remove the SIP distortion of a FITS extension
        """

        self.hdr_logger.debug("Removing SIP distortion from (%s, %s)"
                     % (ext.name, ext._extver))
        for prefix in ['A', 'B', 'AP', 'BP']:
            try:
                order = ext.header[prefix + '_ORDER']
                del ext.header[prefix + '_ORDER']
            except KeyError:
                continue
            for i in range(order + 1):
                for j in range(order + 1):
                    key = prefix + '_%d_%d' % (i, j)
                    try:
                        del ext.header[key]
                    except KeyError:
                        pass
        try:
            del ext.header['IDCTAB']
        except KeyError:
            pass

    def _removeLUT(self, ext):
        """
        Remove the Lookup Table distortion of a FITS extension
        """

        self.hdr_logger.debug("Removing LUT distortion from (%s, %s)"
                     % (ext.name, ext._extver))
        try:
            cpdis = ext.header['CPDIS*']
        except KeyError:
            return
        try:
            for c in range(1, len(cpdis) + 1):
                del ext.header['DP%s.*...' % c]
                del ext.header[cpdis[c - 1].key]
            del ext.header['CPERR*']
            del ext.header['NPOLFILE']
            del ext.header['NPOLEXT']
        except KeyError:
            pass

    def _removeD2IM(self, ext):
        """
        Remove the Detector to Image correction of a FITS extension
        """

        self.hdr_logger.debug("Removing D2IM correction from (%s, %s)"
                     % (ext.name, ext._extver))
        d2imkeys = ['D2IMFILE', 'AXISCORR', 'D2IMEXT', 'D2IMERR']
        for k in d2imkeys:
            try:
                del ext.header[k]
            except KeyError:
                pass

    def _removeAltWCS(self, dest, ext):
        """
        Remove Alternate WCSs of a FITS extension.
        A WCS with wcskey 'O' is never deleted.
        """
        dkeys = altwcs.wcskeys(dest[('SCI', 1)].header)
        self.hdr_logger.debug("Removing alternate WCSs with keys %s from %s"
                     % (dkeys, dest.filename()))
        for k in dkeys:
            altwcs.deleteWCS(dest, ext=ext, wcskey=k)

    def _removePrimaryWCS(self, ext):
        """
        Remove the primary WCS of a FITS extension
        """

        self.hdr_logger.debug("Removing Primary WCS from (%s, %s)"
                     % (ext.name, ext._extver))
        naxis = ext.header.ascard['NAXIS'].value
        for key in basic_wcs:
            for i in range(1, naxis + 1):
                try:
                    del ext.header.ascard[key + str(i)]
                except KeyError:
                    pass
        try:
            del ext.header.ascard['WCSAXES']
        except KeyError:
            pass

    def _removeIDCCoeffs(self, ext):
        """
        Remove IDC coefficients of a FITS extension
        """

        self.hdr_logger.debug("Removing IDC coefficient from (%s, %s)"
                     % (ext.name, ext._extver))
        coeffs = ['OCX10', 'OCX11', 'OCY10', 'OCY11', 'IDCSCALE']
        for k in coeffs:
            try:
                del ext.header.ascard[k]
            except KeyError:
                pass


class HeaderletHDU(pyfits.hdu.base.NonstandardExtHDU):
    """
    A non-standard extension HDU for encapsulating Headerlets in a file.  These
    HDUs have an extension type of HDRLET and their EXTNAME is derived from the
    Headerlet's HDRNAME.

    The data itself is a tar file containing a single file, which is the
    Headerlet file itself.  The file name is derived from the HDRNAME keyword,
    and should be in the form `<HDRNAME>_hdr.fits`.  If the COMPRESS keyword
    evaluates to `True`, the tar file is compressed with gzip compression.

    The Headerlet contained in the HDU's data can be accessed by the
    `headerlet` attribute.
    """

    _extension = 'HDRLET'

    @pyfits.util.lazyproperty
    def data(self):
        size = self.size()
        self._file.seek(self._datLoc)
        return self._file.readarray(size)

    @pyfits.util.lazyproperty
    def headerlet(self):
        self._file.seek(self._datLoc)
        s = StringIO()
        # Read the data into a StringIO--reading directly from the file
        # won't work (at least for gzipped files) due to problems deep
        # within the gzip module that make it difficult to read gzip files
        # embedded in another file
        s.write(self._file.read(self.size()))
        s.seek(0)
        if self._header['COMPRESS']:
            mode = 'r:gz'
        else:
            mode = 'r'
        t = tarfile.open(mode=mode, fileobj=s)
        members = t.getmembers()
        if not len(members):
            raise ValueError('The Headerlet contents are missing.')
        elif len(members) > 1:
            warnings.warn('More than one file is contained in this '
                          'only the headerlet file should be present.')
        hlt_name = self._header['HDRNAME'] + '_hdr.fits'
        try:
            hlt_info = t.getmember(hlt_name)
        except KeyError:
            warnings.warn('The file %s was missing from the HDU data.  '
                          'Assuming that the first file in the data is '
                          'headerlet file.' % hlt_name)
            hlt_info = members[0]
        hlt_file = t.extractfile(hlt_info)
        # hlt_file is a file-like object
        return Headerlet(hlt_file, mode='readonly')

    @classmethod
    def fromheaderlet(cls, headerlet, compress=False):
        """
        Creates a new HeaderletHDU from a given Headerlet object.

        Parameters
        ----------
        headerlet : Headerlet
            A valid Headerlet object.
        compress : bool, optional
            Gzip compress the headerlet data.
        """

        phdu = headerlet[0]
        phduhdr = phdu.header
        hlt_filename = phdu.header['HDRNAME'] + '_hdr.fits'

        # TODO: As it stands there's no good way to write out an HDUList in
        # memory, since it automatically closes the given file-like object when
        # it's done writing.  I'd argue that if passed an open file handler it
        # should not close it, but for now we'll have to write to a temp file.
        fd, name = tempfile.mkstemp()
        try:
            f = os.fdopen(fd, 'rb+')
            headerlet.writeto(f)
            # The tar file itself we'll write in memory, as it should be
            # relatively small
            if compress:
                mode = 'w:gz'
            else:
                mode = 'w'
            s = StringIO()
            t = tarfile.open(mode=mode, fileobj=s)
            t.add(name, arcname=hlt_filename)
            t.close()
        finally:
            os.remove(name)

        cards = [
            pyfits.Card('XTENSION', cls._extension, 'Headerlet extension'),
            pyfits.Card('BITPIX',    8, 'array data type'),
            pyfits.Card('NAXIS',     1, 'number of array dimensions'),
            pyfits.Card('NAXIS1',    len(s.getvalue()), 'Axis length'),
            pyfits.Card('PCOUNT',    0, 'number of parameters'),
            pyfits.Card('GCOUNT',    1, 'number of groups'),
            pyfits.Card('EXTNAME', cls._extension,
                        'name of the headerlet extension'),
            phdu.header.ascard['HDRNAME'],
            phdu.header.ascard['DATE'],
            pyfits.Card('SIPNAME', headerlet['SIPWCS', 1].header['WCSNAMEA'],
                        'SIP distortion model name'),
            phdu.header.ascard['NPOLFILE'],
            phdu.header.ascard['D2IMFILE'],
            pyfits.Card('COMPRESS', compress, 'Uses gzip compression')
        ]

        header = pyfits.Header(pyfits.CardList(cards))

        hdu = cls(data=pyfits.DELAYED, header=header)
        # TODO: These hacks are a necessary evil, but should be removed once
        # the pyfits API is improved to better support specifying a backing
        # file object for an HDU
        hdu._file = pyfits.file._File(s)
        hdu._datLoc = 0
        return hdu

    @classmethod
    def match_header(cls, header):
        """
        This is a class method used in the pyfits refactoring branch to
        recognize whether or not this class should be used for instantiating
        an HDU object based on values in the header.

        It is included here for forward-compatibility.
        """

        card = header.ascard[0]
        if card.key != 'XTENSION':
            return False
        xtension = card.value.rstrip()
        return xtension == cls._extension

    # TODO: Add header verification

    def _summary(self):
        # TODO: Perhaps make this more descriptive...
        return (self.name, self.__class__.__name__, len(self._header.ascard))

pyfits.register_hdu(HeaderletHDU)