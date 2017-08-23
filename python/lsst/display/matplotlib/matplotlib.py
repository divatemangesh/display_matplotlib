#
# LSST Data Management System
# Copyright 2008, 2009, 2010, 2015 LSST Corporation.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#

##
## \file
## \brief Definitions to talk to matplotlib from python using the "afwDisplay" interface

from __future__ import absolute_import, division, print_function

import math
import os
import re
import sys
import time

import matplotlib.pyplot as pyplot
import matplotlib.colors as mpColors
import numpy as np
import numpy.ma as ma

import lsst.afw.display as afwDisplay
import lsst.afw.display.rgb as afwRgb
import lsst.afw.display.interface as interface
import lsst.afw.display.virtualDevice as virtualDevice
import lsst.afw.display.ds9Regions as ds9Regions
import lsst.afw.image as afwImage

import lsst.afw.geom as afwGeom

try:
    _mpFigures
except NameError:
    _mpFigures = {0 : None}              # matplotlib (actually pyplot) figures
    eventHandlers = {}                  # event handlers for matplotlib figures

def getMpFigure(fig=None, clear=True):
    """Return a pyplot figure(); if fig is supplied save it and make it the default
    fig may also be a bool (make a new figure) or an int (return or make a figure (1-indexed;
    python-list style -n supported)
    """

    if not pyplot:
        raise RuntimeError("I am unable to plot as I failed to import matplotlib")

    if not isinstance(fig, int):
        raise RuntimeError("I'm sorry, but matplotlib uses integers to identify plots")

    i = fig
    if i == 0:
        raise RuntimeError("I'm sorry, but matplotlib uses 1-indexed figures")
    if i < 0:
        try:
            i = sorted(_mpFigures.keys())[i] # simulate list's [-n] syntax
        except IndexError:
            if _mpFigures:
                print("Illegal index: %d" % i, file=sys.stderr)
            i = 1

    def lift(fig):
        fig.canvas._tkcanvas._root().lift() # == Tk's raise, but raise is a python reserved word

    if _mpFigures.has_key(i):
        try:
            lift(_mpFigures[i])
        except Exception, e:
            del _mpFigures[i]

    if not _mpFigures.has_key(i):
        for j in range(1, i):
            getMpFigure(j, clear=False)

        _mpFigures[i] = pyplot.figure()
        #
        # Modify pyplot.figure().show() to make it raise the plot too
        #
        def show(self, _show=_mpFigures[i].show):
            _show(self)
            try:
                lift(self)
            except Exception, e:
                pass
        # create a bound method
        import types
        _mpFigures[i].show = types.MethodType(show, _mpFigures[i], _mpFigures[i].__class__)

    fig = _mpFigures[i]

    if not fig:
        i = sorted(_mpFigures.keys())[0]
        if i > 0:
            fig = _mpFigures[i[-1]]
        else:
            fig = getMpFigure(1)

    if clear:
        fig.clf()

    pyplot.figure(fig.number)           # make it active

    return fig

#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-

class DisplayImpl(virtualDevice.DisplayImpl):
    server = None

    def __init__(self, display, verbose=False, interpretMaskBits=True, mtvOrigin=afwImage.PARENT,
                 *args, **kwargs):
        """
        Initialise a matplotlib display

        @param interpretMaskBits    Interpret the mask value under the cursor
        @param mtvOrigin            Display pixel coordinates with LOCAL origin
                                    (bottom left == 0,0 not XY0)
        """
        virtualDevice.DisplayImpl.__init__(self, display, verbose)

        self._figure = getMpFigure(fig=display.frame + 1, clear=True)
        self._display = display
        self._maskTransparency = {None : 0.7}
        self._interpretMaskBits = interpretMaskBits # interpret mask bits in mtv
        self._mtvOrigin = mtvOrigin
        self._xy0 = (0, 0)

        #
        # Support self._scale()
        #
        self._normalize = None
        #
        # Hack to support self._erase() and also reporting pixel/mask values;  set in mtv
        #
        self._image = None

    def __del__(self):
        del _mpFigures[self._display]
    #
    # Extensions to the API
    #
    def show_color_bar(show=True):
        """Show (or hide) the colour bar"""
        self._figure.colorbar(show)
    #
    # Defined API
    #
    def _setMaskTransparency(self, transparency, maskplane):
        """Specify mask transparency (percent)"""

        self._maskTransparency[maskplane] = 0.01*transparency

    def _getMaskTransparency(self, maskplane=None):
        """Return the current mask transparency"""
        return self._maskTransparency[maskplane if maskplane in self._maskTransparency else None]

    def _mtv(self, image, mask=None, wcs=None, title=""):
        """Display an Image and/or Mask on a matplotlib display
        """
        title = str(title) if title else ""

        self._figure.clf()              # calling erase() calls _mtv

        self._i_mtv(image, wcs, title, False)
        ax = self._figure.gca()

        if mask:
            self._i_mtv(mask, wcs, title, True)
            
        if title:
            ax.set_title(title)
        
        self._zoomfac = 1.0
        self._width, self._height = image.getDimensions()
        self._xcen = 0.5*self._width
        self._ycen = 0.5*self._height
        self._xy0 = image.getXY0()
        #
        # I hate to do this, but it's an easy way to make erase() work
        # (I don't know how to just erase the overlays)
        #
        self._image = image
        self._mask = mask
        self._wcs = wcs
        self._title = title
        #
        def format_coord(x, y, x0=self._xy0[0], y0=self._xy0[1],
                         origin=afwImage.PARENT, bbox=self._image.getBBox(afwImage.PARENT)):

            fmt = '(%1.2f, %1.2f)' 
            if self._mtvOrigin == afwImage.PARENT:
                msg = fmt % (x, y)
            else:
                msg = (fmt + "L") % (x - x0, y - y0)

            col = int(x + 0.5)
            row = int(y + 0.5)
            if bbox.contains(afwGeom.PointI(col, row)):
                col -= x0
                row -= y0

                msg += ' %1.3f' % (self._image.get(col, row))
                if self._mask:
                    val = self._mask.get(col, row)
                    if self._interpretMaskBits:
                        msg += " [%s]" % self._mask.interpret(val)
                    else:
                        msg += " 0x%x" % val

            return msg

        ax.format_coord = format_coord
        # Stop images from reporting their value as we've already printed it nicely
        from matplotlib.image import AxesImage
        for a in ax.mouseover_set:
            if isinstance(a, AxesImage):
                a.get_cursor_data = lambda ev: None # disabled

        self._figure.canvas.draw_idle()

    def _i_mtv(self, data, wcs, title, isMask):
        """Internal routine to display an Image or Mask on a DS9 display"""

        title = str(title) if title else ""
        dataArr = data.getArray()

        if isMask:
            maskPlanes = data.getMaskPlaneDict()
            nMaskPlanes = max(maskPlanes.values()) + 1

            planes = {}                      # build inverse dictionary
            for key in maskPlanes:
                planes[maskPlanes[key]] = key

            planeList = range(nMaskPlanes)

            maskArr = np.zeros_like(dataArr, dtype=np.int32)

            colors = ['black']
            colorGenerator = self.display.maskColorGenerator(omitBW=True)
            for p in planeList:
                color = self.display.getMaskPlaneColor(planes[p]) if p in planes else None

                if not color:            # none was specified
                    color = next(colorGenerator)

                colors.append(color)
            #
            # Set the maskArr image to be an index into our colour map (cmap; see below)
            #
            for i, p in enumerate(planeList):
                color = colors[i]
                if color.lower() == "ignore":
                    continue

                maskArr[(dataArr & (1 << p)) != 0] += i + 1 # + 1 as we set colors[0] to black

            #
            # Convert those colours to RGBA so we can have per-mask-plane transparency
            # and build a colour map
            #
            colors = mpColors.to_rgba_array(colors)
            colors[0][3] = 0.0          # it's black anyway
            for i, p in enumerate(planeList):
                colors[i + 1][3] = 1 - self._getMaskTransparency(planes[p] if p in planes else None)

            dataArr = maskArr
            cmap = mpColors.ListedColormap(colors)
            norm = mpColors.NoNorm()
        else:
            cmap = pyplot.cm.gray
            norm = self._normalize

        ax = self._figure.gca()
        bbox = data.getBBox()
        ax.imshow(dataArr, origin='lower', interpolation='nearest',
                  extent=(bbox.getBeginX() - 0.5, bbox.getEndX() - 0.5,
                          bbox.getBeginY() - 0.5, bbox.getEndY() - 0.5),
                  cmap=cmap, norm=norm)

        if False:
            if evData:
                axes = self._figure.get_axes()[0]
                myText = axes.text(0.05, 1.05, 'Press "return" to show intensity here',
                                   transform=axes.transAxes, va='top')
                
                global eventHandlers
                eventHandlers[self._figure] = EventHandler((evData, myText), self._figure)
                
        self._figure.canvas.draw_idle()
    #
    # Graphics commands
    #
    def _buffer(self, enable=True):
        pass

    def _flush(self):
        pass

    def _erase(self):
        """Erase the display"""
        #
        # Rather than erase only the glyphs we'll redraw the image. We don't call
        # self._mtv() as it resets e.g. the zoom
        #
        # This isn't a good solution.
        #
        self._figure.clf()

        if self._image:
            self._i_mtv(self._image, self._wcs, self._title, False)
            if self._mask:
                self._i_mtv(self._mask, self._wcs, self._title, True)
            if self._title:
                self._figure.gca().set_title(self._title)
            self._zoom(self._zoomfac)

        self._figure.canvas.draw_idle()

    def _dot(self, symb, c, r, size, ctype,
             fontFamily="helvetica", textAngle=None):
        """Draw a symbol at (col,row) = (c,r) [0-based coordinates]
    Possible values are:
            +                Draw a +
            x                Draw an x
            *                Draw a *
            o                Draw a circle
            @:Mxx,Mxy,Myy    Draw an ellipse with moments (Mxx, Mxy, Myy) (argument size is ignored)
            An object derived from afwGeom.ellipses.BaseCore Draw the ellipse (argument size is ignored)
    Any other value is interpreted as a string to be drawn. Strings obey the fontFamily (which may be extended
    with other characteristics, e.g. "times bold italic".  Text will be drawn rotated by textAngle (textAngle is
    ignored otherwise).

    N.b. objects derived from BaseCore include Axes and Quadrupole.
    """
        if not ctype:
            ctype = afwDisplay.GREEN

        axis = self._figure.gca()
        x0, y0 = self._xy0
        
        if isinstance(symb, afwGeom.ellipses.BaseCore):
            from matplotlib.patches import Ellipse

            axis.add_artist(Ellipse((c + x0, r + y0), xradius=symb.getA(), yradius=symb.getB(),
                                          rot_deg=math.degrees(symb.getTheta()), color=ctype))
        elif symb == 'o':
            from matplotlib.patches import CirclePolygon as Circle

            axis.add_artist(Circle((c + x0, r + y0), radius=size, color=ctype, fill=False))
        else:
            from matplotlib.lines import Line2D

            for ds9Cmd in ds9Regions.dot(symb, c + x0, r + y0, size, fontFamily="helvetica", textAngle=None):
                tmp = ds9Cmd.split('#')
                cmd = tmp.pop(0).split()
                comment = tmp.pop(0) if tmp else ""

                cmd, args = cmd[0], cmd[1:]
                
                if cmd == "line":
                    args = np.array(args).astype(float) - 1.0

                    x = np.empty(len(args)//2)
                    y = np.empty_like(x)
                    i = np.arange(len(args), dtype=int)
                    x = args[i%2 == 0]
                    y = args[i%2 == 1]

                    axis.add_line(Line2D(x, y, color=ctype))
                elif cmd == "text":
                    x, y = np.array(args[0:2]).astype(float) - 1.0
                    axis.text(x, y, symb, color=ctype,
                              horizontalalignment='center', verticalalignment='center')
                else:
                    raise RuntimeError(ds9Cmd)

    def _drawLines(self, points, ctype):
        """Connect the points, a list of (col,row)
        Ctype is the name of a colour (e.g. 'red')"""

        from matplotlib.lines import Line2D

        if not ctype:
            ctype = afwDisplay.GREEN

        points = np.array(points)
        x = points[:, 0] + self._xy0[0]
        y = points[:, 1] + self._xy0[1]

        self._figure.gca().add_line(Line2D(x, y, color=ctype))
    #
    # Set gray scale
    #
    def _scale(self, algorithm, minval, maxval, unit, *args, **kwargs):
        if algorithm is None:
            self._normalize = None
        elif algorithm == "asinh":
            self._normalize = AsinhNormalize(minimum=minval,
                                             dataRange=maxval - minval, Q=kwargs.get("Q", 0.0))
        else:
            self._normalize = LinearNormalize(minimum=minval, maximum=maxval)
    #
    # Zoom and Pan
    #
    def _zoom(self, zoomfac):
        """Zoom by specified amount"""

        self._zoomfac = zoomfac

        x0, y0 = self._xy0
        x1, y1 = x0 + self._width, y0 + self._height

        size = min(self._width, self._height)
        xmin, xmax = self._xcen + x0 + size/self._zoomfac*np.array([-1, 1])
        ymin, ymax = self._ycen + y0 + size/self._zoomfac*np.array([-1, 1])

        ax = self._figure.gca()
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
            
    def _pan(self, colc, rowc):
        """Pan to (colc, rowc)"""

        self._xcen = colc
        self._ycen = rowc

        self._zoom(self._zoomfac)        

    def XXX_getEvent(self):
        """Listen for a key press, returning (key, x, y)"""

        raise RuntimeError("Write me")

class Normalize(mpColors.Normalize):
    def __init__(self, vmin=None, vmax=None, clip=False, minimum=0, dataRange=1, Q=8):
        mpColors.Normalize.__init__(self, vmin, vmax, clip)

        if True:
            self.mapping = afwRgb.AsinhMapping(minimum, dataRange, Q)
        else:
            self.mapping = afwRgb.LinearMapping(minimum, minimum+dataRange, Q)

    def __call__(self, value, clip=None):
        # Must return a MaskedArray
        data = value.data - self.mapping.minimum[0]
        return ma.array(data*self.mapping.mapIntensityToUint8(data)/255.0)

class AsinhNormalize(Normalize):
    def __init__(self, vmin=None, vmax=None, clip=False, minimum=0, dataRange=1, Q=8):
        Normalize.__init__(self, vmin, vmax, clip)

        self.mapping = afwRgb.AsinhMapping(minimum, dataRange, Q)

class LinearNormalize(Normalize):
    def __init__(self, vmin=None, vmax=None, clip=False, minimum=0, maximum=1):
        Normalize.__init__(self, vmin, vmax, clip)

        self.mapping = afwRgb.LinearMapping(minimum, maximum)
