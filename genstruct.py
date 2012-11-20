#!/usr/bin/env python

"""
GenStruct -- Generation of Structures for fapping.
"""

__version__ = "$Revision$"

import subprocess
import textwrap
import sys
import math
import re
import os
import io
import itertools
import ConfigParser
import functional
from config import Options
from operations import *
import numpy as np
from numpy import array
from elements import WEIGHT
from bookkeeping import * 
from random import random, uniform, randrange, choice
from datetime import date
from logging import warning, debug, error, info, critical
from scipy.spatial import distance

class Bond(object):
    """ Bonds between atoms """
    def __init__(self, bond_from, bond_to, type, distance=None):
        self.frm = bond_from
        self.to = bond_to # can be Atom or ConnectPoint
        self.type = type
        self.vector = bond_to.coordinates[:3] - bond_from.coordinates[:3]
        # note the distance will change for the ConnectPoint bond
        # once the Atom is bonded to another building unit.
        if distance is not None:
            self.distance = distance
        else:
            self.distance = np.linalg.norm(self.vector)
    def __copy__(self):
        dup = object.__new__(Bond)
        dup.__dict__ = self.__dict__.copy()
        dup.frm = None
        dup.to = None

        return dup
        

class Atom(object):
    """
    Contains atomistic information read in from an input file
    """
    def __init__(self, text=None):

        self.coordinates = np.ones(4)

        if text is None:
            self.element = "X"
            self.mass = 0.
            self.force_field_type = None

        else:
            text = text.split()
            # element type (e.g. C)
            self.element = text[0].strip()
            # mass of element
            self.mass = WEIGHT[self.element]
            # cartesian coordinates (x y z)
            self.coordinates[:3] = array([float(i) for i in text[2:5]],
                                 dtype=float)
            # force field atom type read in from an input file
            self.force_field_type = text[1]
        # fractional coordinates (a b c)
        self.scaled_coords = None
        # the bu index this atom belongs to
        self.bu_index = None
        # flag for if the building unit is a metal
        self.bu_metal = False
        # index of functional group (also functions as a flag)
        self.fnl_group_index = None
        # list of bonds which connect atoms 
        self.bonds = []
        # This will reference a connect point of a building unit
        # if it is a connecting atom
        self.connectivity = None
        # index as listed in the SBU
        self.index = 0

    def __copy__(self):
        """
        Copy relevant, standard object data from the existing class,
        i.e. no referencing to other classes will be copied.  This
        means that self.bonds will not contain Atom() classes.
        """
        dup = object.__new__(Atom)
        dup.__dict__ = self.__dict__.copy()
        dup.bonds = []
        return dup

    def __str__(self):
        
        return "Atom %3i, %2s, location: (%9.5f, %9.5f, %9.5f)\n"%(
                tuple([self.index, self.element] + 
                    [i for i in self.coordinates[:3]]))\
                +"Belongs to: %s\nmetal: %s\n"%(str(self.bu_index),
                        str(self.bu_metal))\
                +"Connect atom: %s\n"%str(self.connectivity)


class BuildingUnit(object):
    """
    Contains collections of Atom objects and connectivity
    information for building MOFs
    """

    def __init__(self, name=None, items=[]):
        items = dict(items)
        self.name = name 
        # this is a global index which is reported in the
        # structure name.
        self.index = int(items.pop('index'))
        # keep track of each building unit internally with it's own
        # index.
        self.internal_index = 0
        # keep track of what order the building unit was placed in the
        # MOF
        self.order = 0
        # topology
        self.topology = items.pop('topology')
        # track if metal building unit
        self.metal = items.pop('metal').lower() == "true"

        # in case of special conditions
        if items.has_key('parent'):
            self.parent = items.pop('parent')
        else:
            self.parent = None

        # Topology info:
        # points of connection for other building units
        self.build_connect_points(items.pop('connectivity'))

        # set up atoms and their connectivity
        self.build_atoms(items.pop('coordinates'), 
                          items.pop('table'))

        # determine bonding constraints
        self.build_bond_const(items.pop('bond_constraints'))

        # Special Building Units for multiple versions of the
        # same building unit
        self.specialbu = []

        # centre of mass
        self.calculate_COM()

    def build_atoms(self, coordinates, table):
        """
        Read in coordinates and table strings to build the 
        atoms and connectivity info.
        """
        self.atoms, self.bonds = [], []
        ind = 0
        for atom in coordinates.splitlines():
            atom = atom.strip()
            if not atom:
                continue
            self.atoms.append(Atom(atom))
            self.atoms[-1].index = ind
            self.atoms[-1].bu_index = self.index
            self.atoms[-1].bu_metal = self.metal
            ind += 1

        table = table.strip()
        # self.connect_points must be populated before doing this.
        for bond in table.splitlines():
            bond = bond.strip().split()
            # add the bonding information
            # first two cases are for bonding to connecting points
            if "c" in bond[0].lower():
                connect_ind = int(bond[0].lower().strip('c'))
                atom_ind = int(bond[1])
                self.bonds.append(Bond(
                    self.connect_points[connect_ind],
                    self.atoms[atom_ind], bond[2]))
                # add the Atom to the connect_point
                self.connect_points[connect_ind].atoms.append(
                        self.atoms[atom_ind])
                self.atoms[atom_ind].connectivity = connect_ind

            elif "c" in bond[1].lower():
                # subtract 1 since the input file starts at 1
                connect_ind = int(bond[1].lower().strip('c'))-1
                atom_ind = int(bond[0])
                self.bonds.append(Bond(
                    self.atoms[atom_ind],
                    self.connect_points[connect_ind],
                    bond[2]))
                # add the Atom to the connect_point
                self.connect_points[connect_ind].atoms.append(
                        self.atoms[atom_ind])
                self.atoms[atom_ind].connectivity = connect_ind

            else:
                # add bonding to atoms
                # add the index of the atom it is bonded with
                self.atoms[int(bond[0])].bonds.append(
                        int(bond[1]))
                self.atoms[int(bond[1])].bonds.append(
                        int(bond[0]))
                self.bonds.append(Bond(
                     self.atoms[int(bond[0])], 
                     self.atoms[int(bond[1])], bond[2]))
        
    def build_connect_points(self, connectivity):
        """
        Read connectivity string to build the connectivity
        info.
        """
        connectivity = connectivity.strip()
        self.connect_points = []
        for connpt in connectivity.splitlines():
            order = len(self.connect_points)
            self.connect_points.append(ConnectPoint(connpt))
            self.connect_points[-1].order = order
            if self.metal:
                self.connect_points[-1].metal = True

    def build_bond_const(self, constraints):
        """
        Read in bond constraint info in the order:
        [connect_point.index, connect_point.special]
        """
        constraints = constraints.strip()
        # this assumes connect_points is already established
        for pt in self.connect_points:
            pt.constraint = None
        for const in constraints.splitlines():
            const = const.strip().split()
            for pt in self.connect_points:
                if pt.index == int(const[0]):
                    pt.constraint = int(const[1])

    def snap_to(self, self_point, connect_point):
        """
        adjusts atomic coordinates to orient correctly with
        connect_point
        """
        angle = calc_angle(self_point.para[:3], -connect_point.para[:3])
        # in case the angle is zero
        if np.allclose(angle, 0.):
            trans_v = connect_point.coordinates - \
                      self_point.coordinates

            for atom in self.atoms:
                atom.coordinates += trans_v
            for cp in self.connect_points:
                cp.coordinates += trans_v

            return

        axis = np.cross(self_point.para[:3], connect_point.para[:3])
        axis = axis / length(axis)

        if np.allclose(self.COM, np.zeros(3)):
            pt = None
        else:
            pt = self.COM[:]
        R = rotation_matrix(axis, angle, point=pt)
        # TODO(pboyd): applying this transform one by one is 
        # really inefficient, and should be done to a giant array
        trans_v = connect_point.coordinates - np.dot(R, 
                                self_point.coordinates)
        for atom in self.atoms:
            atom.coordinates = np.dot(R, atom.coordinates) + trans_v

        for cp in self.connect_points:
            cp.coordinates = np.dot(R, cp.coordinates) + trans_v
            # apply rotation matrix to vectors
            cp.para[:3] = np.dot(R[:3,:3], cp.para[:3])
            cp.perp[:3] = np.dot(R[:3,:3], cp.perp[:3])

    def align_to(self, self_point, connect_point):
        """
        aligns two building units along their perp vectors
        """
        angle = calc_angle(self_point.perp[:3], connect_point.perp[:3])
        if np.allclose(angle, 0.):
            return
        axis = connect_point.para[:3] 
        R = rotation_matrix(axis, angle, point=
                            self_point.coordinates)
        tol = min(angle, 0.02)
        if not np.allclose(calc_angle(np.dot(R[:3,:3], 
                           self_point.perp[:3]), 
                           connect_point.perp),0., atol=tol):
            angle = -angle 
            R = rotation_matrix(axis, angle, 
                                point=self_point.coordinates)

        for atom in self.atoms:
            atom.coordinates = np.dot(R, atom.coordinates)
        for cp in self.connect_points:
            cp.coordinates = np.dot(R, cp.coordinates)
            cp.para[:3] = np.dot(R[:3,:3], cp.para[:3])
            cp.perp[:3] = np.dot(R[:3,:3], cp.perp[:3])

    def calculate_COM(self):
        self.COM = \
            np.average(array([atom.coordinates[:3] for atom in self.atoms]), 
                       axis=0, 
                       weights=array([atom.mass for atom in self.atoms]))
        return

    def __str__(self):
        line = "Building Unit from Genstruct: %s\n"%self.name
        for atom in self.atoms:
            line += "%3s%9.3f%7.3f%7.3f"%tuple([atom.element] + 
                            list(atom.coordinates[:3]))
            line += " UFF type: %s\n"%(atom.force_field_type)
        line += "Connectivity Info:\n"
        for con in self.connect_points:
            line += "%3i%9.3f%7.3f%7.3f"%tuple([con.index] +
                        list(con.coordinates[:3]))
            line += " Special bonding: %5s"%str(con.special)
            line += " Symmetry flag: %s\n"%str(con.symmetry)
        return line

    def __copy__(self):
        dup = object.__new__(BuildingUnit)
        dup.__dict__ = self.__dict__.copy()
        dup.atoms = []
        dup.connect_points = []
        dup.bonds = []
        return dup


class ConnectPoint(object):
    """
    Contains one point of connection, including associated
    parallel and perpendicular alignment vectors

    In the input file the connect_point should read:
    [index] point[x,y,z] vector[x,y,z] vector[x,y,z] [integer] [integer]
    """

    def __init__(self, text=None):
        if text is None:
            self.index = 0  # index for determining bonding data
            self.coordinates = np.ones(4)   # the location of the connect point
            self.para = np.ones(4) # parallel bonding vector
            self.perp = np.ones(4) # perpendicular bonding vector
            self.atoms = []     # store the atom(s) which connect to this point
            self.special = None # for flagging any special type of bond
        else:
            text = text.strip().split()
            # index will be important for flagging specific bonding
            # and obtaining the connectivity table at the end.
            self.index = int(text[0])
            # point of connection
            self.coordinates = np.ones(4)
            self.coordinates[:3] = array([float(x) for x in text[1:4]])
            # parallel alignment vector
            self.para = np.ones(4)
            self.para[:3] = array([float(z) for z in text[4:7]])
            self.para[:3] = self.para[:3] / length(self.para[:3])
            # perpendicular alignment vector
            self.perp = np.ones(4)
            self.perp[:3] = array([float(y) for y in text[7:10]]) 
            self.perp[:3] = self.perp[:3] / length(self.perp[:3])

        self.order = 0  # keep track of what list order the CP is in the BU
        self.metal = False  # for flagging if the associated BU is metal
        self.bu_order = 0   # for keeping track of what order the BU is 
        # list of bonding atoms.  In most cases this will
        # be only one, but there are exceptions (Ba MOF)
        self.atoms = []
        # flag for if the point has been bonded
        self.bonded = False
        # flag for if the bond is found after the bu is added
        self.bond_from_search = False
        try:
            self.special = int(text[11])
        except:
            self.special = None

        # constraint constrains this connectivity point to a 
        # particular special bond index
        self.constraint = None

        # symmetry type flag
        # other connect_points which it is equivalent (of the same 
        # building unit) will have the same self.equiv value
        try:
            self.symmetry = int(text[10])
        except:
            self.symmetry = 1
        # store what symmetry type of bond has been formed, this should
        # include both the internal indices of the bu and the symmetry labels
        self.bond_label = None 

    def __str__(self):
        if not self.special:
            spec = 0
        else:
            spec = self.special
        if not self.constraint:
            const = 0
        else:
            const = self.constraint
        string = "Bond %i (%5.2f, %5.2f, %5.2f)\n"%tuple([self.index] +
                list(self.coordinates[:3]))
        string += "Symmetry: %2i, Special: %2i, Constraint: %2i"%(
                self.symmetry, spec, const)
        return string

    def __copy__(self):
        """
        Designed to copy relevant information only, no reference to 
        other classes, such as the information contained in self.atoms
        """
        dup = object.__new__(ConnectPoint)
        dup.__dict__ = self.__dict__.copy()
        dup.atoms = []
        return dup


class Structure(object):
    """
    Contains building units to generate a MOF
    """
    def __init__(self):
        self.building_units = []
        self.cell = Cell()
        self.debug_lines = ""
        self.natoms = 0
        # Store global bonds here
        self.bonds = []
        self.sym_id = []

    def debug_xyz(self):
        cellformat = "H%12.5f%12.5f%12.5f " + \
                "atom_vector%12.5f%12.5f%12.5f\n"
        bondformat = "H%12.5f%12.5f%12.5f " + \
                "atom_vector%12.5f%12.5f%12.5f " + \
                "atom_vector%12.5f%12.5f%12.5f\n"
        atomformat = "%s%12.5f%12.5f%12.5f\n"

        lines = []
        [lines.append(cellformat%tuple(list(self.cell.origin[ind]) + 
                      list(self.cell.lattice[ind]))) for ind in range(
                      self.cell.index)]
        for bu in self.building_units:
            [lines.append(bondformat%tuple(list(c.coordinates[:3]) +
                list(c.para[:3]) + list(c.perp[:3]))) for c in bu.connect_points]
            [lines.append(atomformat%tuple([a.element] + 
                list(a.coordinates[:3]))) for a in bu.atoms]

        n = len(lines)

        self.debug_lines += "%5i\ndebug\n"%n
        self.debug_lines += "".join(lines)

    def insert(self, bu, bond, add_bu, add_bond):

        self.building_units.append(add_bu)   # append new BU to existing list
        self.debug_xyz()  # store coordinates
        add_bu.snap_to(add_bond, bond)  # align the para vectors
        order = len(self.building_units) - 1  # order of the new building unit
        add_bu.order = order
        # asign the bu order to the connect points in the bu
        for cp in add_bu.connect_points:
            cp.bu_order = order
        self.debug_xyz()  # store coordinates
        add_bu.align_to(add_bond, bond)  # align the perp vectors
        # re-order atom indices within bu.atoms
        # to coincide with connectivity
        for atom in add_bu.atoms:
            # adjust the atom index
            atom.index += self.natoms
            # adjust the bonding indices for each atom
            for bidx in atom.bonds:
                bidx += self.natoms
        # update the number of atoms in the structure
        self.natoms += len(add_bu.atoms)
        # introduce bond between joined connectivity points
        self.update_connectivities(bu, bond, add_bu, add_bond)
        # check for bonding between other existing building units
        self.bonding()
        self.debug_xyz()  # store coordinates
        return

    def bonding(self):
        """
        Check for new periodic boundaries, bonds using existing periodic boundaries,
        or local bonds.
        """
        connect_pts = [cp for bu in self.building_units for 
                       cp in bu.connect_points]
        bond_combos = itertools.combinations(connect_pts, 2)
        for cp1, cp2 in bond_combos:
            ibu1 = cp1.bu_order 
            bu1 = self.building_units[ibu1]
            ibu2 = cp2.bu_order
            bu2 = self.building_units[ibu2]
            if valid_bond(bu1, cp1, bu2, cp2):
                if self.is_aligned(cp1, cp2):
                    # determine the vector between connectpoints
                    dvect = (cp2.coordinates[:3] - 
                             cp1.coordinates[:3])
                    # get a shifted vector to see if the bond will
                    # be local
                    svect = self.cell.periodic_shift(dvect.copy())

                    # test for local bond
                    if np.allclose(length(svect), 0., atol=0.2):
                        debug("local bond found between "
                              +"#%i %s, bond %i and "
                              %(ibu1, bu1.name, cp1.index)
                              +"#%i %s, bond %i."
                              %(ibu2, bu2.name, cp2.index))
                        # join two bonds
                        self.update_connectivities(bu1, cp1, bu2, cp2)
                        cp1.bond_from_search = True
                        cp2.bond_from_search = True
                    elif self.cell.valid_vector(dvect):
                        debug("periodic vector found: " 
                        +"(%9.5f, %9.5f, %9.5f)"%(
                            tuple(dvect.tolist()))
                        +" between #%i %s, bond %i"%(
                            ibu1, bu1.name, cp1.index)
                        +" and #%i %s, bond %i"%(
                            ibu2, bu2.name, cp2.index))
                        self.cell.origin[self.cell.index] =\
                                cp1.coordinates[:3].copy()
                        self.cell.add_vector(dvect)
                        self.update_connectivities(bu1, cp1, 
                                                 bu2, cp2)
                        cp1.bond_from_search = True
                        cp2.bond_from_search = True
                    else:
                        debug("No bonding found with "
                        +"original (%9.5f, %9.5f, %9.5f),"
                        %(tuple(dvect.tolist()))+
                        " shifted (%9.5f, %9.5f, %9.5f)"
                        %(tuple(svect.tolist())))

    def is_aligned(self, cp1, cp2):
        """Return True if the connect_points between bu1 and bu2 are
        parallel.

        """
        if parallel(-cp1.para, cp2.para, tol=0.2): 
            if parallel(cp1.perp, cp2.perp, tol=0.2):
                return True
        return False

    def overlap_bu(self, bu):
        """
        Check the bu supplied for overlap with all other atoms
        """
        # scale the van der waals radii by sf
        sf = 0.004
        for atom in bu.atoms:
            elem, coords = self.min_img_shift(atom=atom.coordinates)
            # debug to see if the shift is happening
            #check = np.append([atom.coordinates[:3]], coords, axis=0)
            #writexyz(["Bi"]+elem, check)
            #writexyz([i.element for i in bu.atoms], [i.coordinates[:3]\
            #          for i in bu.atoms], name = "noncorect")
            # distance checks
            distmat = distance.cdist([atom.coordinates[:3]], coords)
            # check for atom == atom, bonded
            excl = [atom.index] + [idx for idx in atom.bonds]
            for idx, dist in enumerate(distmat[0]):
                if idx not in excl:
                    if dist < (Radii[atom.element]+ Radii[elem[idx]])*sf:
                        return True
        return False

    def min_img_shift(self, atom=np.zeros(3)):
        """
        shift all atoms in the building units to within the periodic
        bounds of the atom supplied
        """
        mof_coords = np.zeros((self.natoms, 3))
        elements = []
        if self.cell.index:
            fatom = np.dot(atom[:3], self.cell.ilattice)
            atcount, max = 0, 0
            for bu in self.building_units:
                elements += [axx.element for axx in bu.atoms]
                max = atcount + len(bu.atoms)
                fcoord = array([np.dot(a.coordinates[:3], self.cell.ilattice)
                            for a in bu.atoms])
                # shift to within the pbc's of atom coords
                rdist = np.around(fatom-fcoord)
                if self.cell.index < 3:
                    # orthogonal projection within cell boundaries
                    shift = np.dot(rdist, 
                        self.cell.lattice[:self.cell.index])
                    at = (array([a.coordinates[:3] for a in bu.atoms])
                      + shift)
                else:
                    at = np.dot(fcoord+rdist, self.cell.lattice[:self.cell.index])
                mof_coords[atcount:max] = at
                atcount += len(bu.atoms) 
        else:
            elements += [axx.element for bux in self.building_units for
                         axx in bux.atoms]
            mof_coords = array([a.coordinates[:3] for s in 
                                self.building_units for a in s.atoms])

        return elements, mof_coords 

    def atom_min_img_shift(self, pos1, pos2):
        """
        shift x,y,z position from pos2 to within periodic boundaries
        of pos1.  Return the shifted coordinates.
        """
        if self.cell.index:
            fpos1 = np.dot(pos1[:3], self.cell.ilattice)
            fpos2 = np.dot(pos2[:3], self.cell.ilattice)
            # shift to within the pbc's of atom coords
            rdist = np.around(fpos1-fpos2)
            if self.cell.index < 3:
                # orthogonal projection within cell boundaries
                shift = np.dot(rdist, 
                        self.cell.lattice[:self.cell.index])
                return pos2[:3] + shift
            else:
                return np.dot(fpos2+rdist, self.cell.lattice[:self.cell.index])

        else:
            return pos2[:3]

    def saturated(self):
        """ return True if all bonds in the structure are bonded """

        if len([1 for bu in self.building_units for 
                cp in bu.connect_points if not cp.bonded]) == 0:
            return True
        return False

    def update_connectivities(self, bu, cp, add_bu, add_cp):
        """ 
        update the atom.bond and bu.bonds when a bond is formed between
        two building units.
        """
        # bond tolerance between two atoms.  This will only be relevant
        # when there is more than one atom associated with a particular
        # bond. Eg. Ba2+
        sf = 0.6
        # determine the symmetry label
        symmetry_label = [(bu.internal_index, cp.symmetry), 
                          (add_bu.internal_index, add_cp.symmetry)]
        symmetry_label.sort()
        symmetry_label = tuple(symmetry_label)
        cp.bonded, add_cp.bonded = True, True
        cp.bond_label, add_cp.bond_label = symmetry_label, symmetry_label
        # join all inter-building unit bonds to the global bonds.
        for bond in add_bu.bonds:
            # check if bonds are to Atom()s or to ConnectPoint()s
            if isinstance(bond.frm, ConnectPoint) or \
                    isinstance(bond.to, ConnectPoint):
                pass
            else:
                self.bonds.append((bond.frm.index, bond.to.index,
                                   bond.type, bond.distance))
        for atm in cp.atoms:
            for atm2 in add_cp.atoms:
                # periodic shift atm2 in terms of atm to calculate distances
                shiftcoord = self.atom_min_img_shift(atm.coordinates, 
                                                    atm2.coordinates)
                dist = length(atm.coordinates, shiftcoord)
                if dist < (Radii[atm.element] + Radii[atm2.element])*sf:
                    # append self.bonds with both atoms.
                    atm.bonds.append(atm2.index)
                    atm2.bonds.append(atm.index)
                    # append to bonds
                    # bond type "single" for now...
                    self.bonds.append((atm.index, atm2.index, "S", dist))
                    #TODO(pboyd): add check for if no Bond is associated
                    # with the connect_point, in which case raise a 
                    # warning. (something wrong with the input)

    def get_scaled(self):
        """
        Add scaled coordinates to all of the atoms in the structure.
        This should be done once all of the periodic boundaries are set.
        """
        for bu in self.building_units:
            for atm in bu.atoms:
                atm.scaled_coords = self.cell.get_scaled(atm.coordinates)
        return

    def __copy__(self):
        """
        Overrides the method of deepcopy, which fails with these
        structures if beyond a certain building unit count.
        Classes which need proper referencing:
 
        self.building_units --> BuildingUnit()
            bu.connect_points --> ConnectPoint()
                cp.atoms --> Atom()
            bu.atoms --> Atom()
                atom.bonds --> Atom()
            bu.bonds --> Bond()
                bond.frm --> Atom(), ConnectPoint()
                bond.to --> Atom(), ConnectPoint()
        self.bonds --> Bond()
            bond.frm --> Atom()
            bond.to --> Atom()
        self.cell --> Cell()
        """
        # Go through each class Structure references and make sure
        # the connections remain intact.
        dup = object.__new__(Structure)
        dup.__dict__ = self.__dict__.copy()
        dup.building_units = []
        dup.cell = deepcopy(self.cell)
        # populate class lists
        # Atom()s can be made to a 1d list because they have 
        # a unique index which can tie them to any building
        # unit
        cats = [copy(atm) for bu in self.building_units for atm
                in bu.atoms]

        for bu in self.building_units:
            copybu = copy(bu)
            ccps = [copy(cp) for cp in bu.connect_points]
            for atm in bu.atoms:
                cat = [i for i in cats if atm.index == i.index]
                if len(cat) == 0 or len(cat) > 1:
                    error("problem copying atoms")
                cat = cat[0]
                cat.bonds = atm.bonds[:]
                copybu.atoms.append(cat)

            for cp in bu.connect_points:
                ccp = [i for i in ccps if cp.index == i.index]
                if len(ccp) == 0 or len(ccp) > 1:
                    error("problem copying connect points")
                ccp = ccp[0]
                atoms = [copyatom for cpatm in cp.atoms for 
                         copyatom in cats if cpatm.index == 
                         copyatom.index]
                ccp.atoms = atoms
                copybu.connect_points.append(ccp)

            for bond in bu.bonds:
                cpbond = copy(bond)
                if isinstance(bond.to, ConnectPoint):
                    to = [cp for cp in ccps if cp.index == bond.to.index]
                    to = to[0]
                elif isinstance(bond.to, Atom):
                    to = [axx for axx in cats if axx.index 
                                            == bond.to.index]
                    to = to[0]
                if isinstance(bond.frm, ConnectPoint):
                    frm = [cp for cp in ccps if cp.index
                                       == bond.frm.index]
                    frm = frm[0]
                elif isinstance(bond.frm, Atom):
                    frm = [axx for axx in cats if axx.index
                                          == bond.frm.index]
                    frm = frm[0]
                cpbond.to = to
                cpbond.frm = frm
                copybu.bonds.append(cpbond)

            dup.building_units.append(copybu)

        #for bond in self.bonds:
        #    cbond = copy(bond)
        #    to = [axx for axx in cats if axx.index
        #            == bond.to.index]
        #    to = to[0]
        #    frm = [axx for axx in cats if axx.index
        #            == bond.frm.index]
        #    frm = frm[0]

        #    cbond.to = to
        #    cbond.frm = frm
        #    dup.bonds.append(cbond)
        return dup

class Cell(object):
    """
    Contains vectors which generate the boundary conditions
    for a particular structure
    """
    def __init__(self):
        self.lattice = np.identity(3) # lattice vectors
        self.params = np.zeros(6) # a, b, c, alpha, beta, gamma
        self.ilattice = np.identity(3) # inverse of lattice
        self.nlattice = np.identity(3) # normalized vectors
        self.index = 0  # keep track of how many vectors have been added
        self.origin = np.zeros((3,3)) # origin for cell vectors

    def get_inverse(self):
        """ Calculates the inverse matrix of the lattice"""
        M = np.matrix(self.lattice)
        self.ilattice = array(M[:self.index].I)
    
    def valid_vector(self, vector):
        """ 
        Checks to see if a vector can be added to the periodic
        boundary conditions.
        """
        normvect = unit_vector(vector[:3])

        for cellv in self.nlattice[:self.index]:
            if np.allclose(np.dot(cellv, normvect), 1):
                debug("vector a linear combination of existing"
                           +" boundaries")
                return False
            #check for co-planar vector
        if self.index == 2:
            if self.planar(normvect):
                debug("vector a linear combination of existing"
                           +" boundaries")
                return False

        elif self.index == 3:
            return False
        
        return True

    def planar(self, vector):
        test = np.dot(vector, np.cross(self.nlattice[0], 
                                       self.nlattice[1]))
        return np.allclose(test, 0)

    def add_vector(self, vector):
        """Adds a vector to the cell"""
        self.lattice[self.index,:] = vector.copy()
        self.nlattice[self.index,:] = unit_vector(vector)
        self.index += 1
        self.get_inverse()

    def periodic_shift(self, vector):
        """
        Shifts a vector to within the bounds of the periodic vectors
        """
        if self.index:
            # get fractional of vector
            proj_vect = np.dot(vector[:3],
                                self.ilattice)
            proj_vect = np.rint(proj_vect)

            shift_vect = np.dot(proj_vect, self.lattice[:self.index])

            # convert back to cartesians
            return (vector - shift_vect)

        return vector

    def get_scaled(self, vector):
        """
        Only applies if the periodic box is fully formed.
        """
        if self.index == 3:
            return np.dot(vector[:3], self.ilattice)

        return np.zeros(3)

    def __copy__(self):
        dup = object.__new__(Cell)
        dup.__dict__ = self.__dict__.copy()
        return dup

class Database(list):
    """
    Reads in a set of Building Units from an input file
    """

    def __init__(self, filename):
        self.readfile(filename)
        # get extension of file name without leading directories.
        self.extension = filename.split('/')[-1]
    def readfile(self, filename):
        """
        Populate the list with building units from the
        input file.
        """
        # Multidict is in bookkeeping.py to account for duplicate
        # names but mangles all the names up, so i will implement if
        # needed, but otherwise we'll just use neat 'ol dict
        #file = ConfigParser.SafeConfigParser(None, Multidict)
        file = ConfigParser.SafeConfigParser()
        file.read(filename)
        for idx, building_unit in enumerate(file.sections()):
            self.append(BuildingUnit(
                                    name=building_unit,
                                    items=file.items(building_unit)))
            self[-1].internal_index = idx
        # special considerations for linked building units
        for ind, bu in enumerate(self):
            if bu.parent:
                # link up this building unit with it's parent
                parent = [i for i in self 
                          if i.name == bu.parent]
                if len(parent) != 1:
                    # raise error
                    error("Multiple building units with the same name!")
                parent[0].specialbu.append(deepcopy(bu))
                self.pop(ind)

        
class Generate(object):
    """
    Algorithm for generating MOFs
    things to keep in mind: the format for the structures are
    totally different now so make sure that all peripheral functions
    can work with the new data (eg. coordinates are now drawn from 
    Atom.coordinates). Things to transfer:
    1)symmetry library
    2)csv
    3)debugging
    4)cif file writing without symmetry
    5)connection tables
    6)functional groups
    """

    def __init__(self, building_unit_database, num=3):
        self.bu_database = building_unit_database
        # select 1 metal and 2 organic linkers to mix
        # for the sampling.

        combinations = self.set_combinations(num)
        # filter out multiple metal combo's
        combinations = self.filter(combinations)
        for combo in combinations:
            building_units = [building_unit_database[i] for i in combo]
            self.exhaustive_sampling(building_units)

    def set_combinations(self, num):
        """ Generates all combinations of a database with length n"""
        indices = [i for i in range(len(self.bu_database))]
        return list(itertools.combinations_with_replacement(indices, num))
    
    def filter(self, list):
        return [j for j in list if self.metcount(j) == 1]

    def metcount(self, combo):
        return len([i for i in combo if self.bu_database[i].metal])
    
    def exhaustive_sampling(self, bu_db):
        """
        Try every combination of bonds which are allowed in a
        tree-type generation where every new iteration over the
        structures should have the same number of building units
        in them.
        """
        # for timing purposes
        stopwatch = Time()
        stopwatch.timestamp()
        # create a local instance of bu_db
        blocks = deepcopy(bu_db)
        for bu in bu_db:
            if bu.specialbu:
                blocks += [i for i in bu.specialbu]

        # assign some indices etc..
        for id, bu in enumerate(blocks):
            # assign a temporary value for bu
            bu.internal_index = id
            for cp in bu.connect_points:
                cp.bu_order = id
        # first scan the database of building units to make sure
        # that all the restricted bonding parameters can be 
        # partnered.
        
        # random seed 
        structures = self.random_insert(blocks)
        done = False
        # while loop
        while not done:
            add_list = []
            # keep track of symmetry labelling so redundant structures are
            # not made.
            symtrack = []
            for id, struct in enumerate(structures):
                # scan over all building units
                bus = struct.building_units
                # scan over all bonds with building units
                bonds = [bond for bu in bus for bond in bu.connect_points]
                # scan over all library
                newbonds = [bond for bu in blocks for bond in bu.connect_points]
                # keep a list of all possible combinations
                bondlist = self.gen_bondlist(bonds, newbonds)
                # print it as a debug.
                debug("Trying %i possible bonds"%(len(bondlist)))
                for bond in bondlist:
                    # bondstate is determined to see if the bond has
                    # already been tried.
                    newstruct = copy(struct)
                    # select the correct instances of the bond and building
                    # units to add.  This is probably slowing down the code
                    # a bit.
                    cbu = newstruct.building_units[bond[0].bu_order]
                    cbond = cbu.connect_points[bond[0].order]
                    add_bu = deepcopy(blocks[bond[1].bu_order])
                    add_bond = add_bu.connect_points[bond[1].order]
                    # determine the symmetry of the bond trying to be formed
                    add_sym = self.get_symmetry_info(cbu, cbond, 
                                                     add_bu, add_bond)
                    new_id = newstruct.sym_id[:]
                    new_id.append(add_sym)
                        
                    if tuple(new_id) not in symtrack:
                        debug("Added building unit #%i %s,"
                            %(len(struct.building_units),
                                add_bu.name)+
                            " bond %i, to building unit"
                            %(add_bond.index) +
                            " #%i  %s, bond %i"
                            %(cbu.order, cbu.name, cbond.index))
                        newstruct.insert(cbu, cbond, 
                                   add_bu, add_bond)
                        if not newstruct.overlap_bu(add_bu):
                            if newstruct.saturated() and \
                                    newstruct.cell.index == 3:
                                stopwatch.timestamp()
                                info("Structure Generated! Timing reports "+
                                     "%f seconds"%stopwatch.timer)
                                newstruct.get_scaled()
                                cif_file = CIF(newstruct, sym=False)
                                cif_file.write_cif()
                                return
                            add_list.append(newstruct)  # append structure to list
                            newstruct.sym_id = new_id[:]
                            # store symmetry data
                            symtrack.append(tuple(newstruct.sym_id[:])) 
                        else:
                            debug("overlap found")
                        if (len(add_list) + len(structures)) > 20000:
                            stopwatch.timestamp()
                            info("Genstruct went too long, "+
                            "%f seconds, returning..."%stopwatch.timer)
                            return
            if not add_list:
                stopwatch.timestamp()
                info("After %f seconds, "%stopwatch.timer +
                "no possible new combinations, returning...")
                return
            for s in add_list:
                write_debug_xyz(s)
            structures = add_list
        return

    def get_symmetry_info(self, bu, cp, add_bu, add_cp):
        """Determine symmmetry, building unit types and distances between
        connect_points to make sure that no redundancy is done in the
        combinatorial growth of MOFs.

        """
        # determine the symmetry label
        symmetry_label = [(bu.internal_index, cp.symmetry), 
                          (add_bu.internal_index, add_cp.symmetry)]
        symmetry_label.sort()
        symmetry_label = tuple(symmetry_label)
        return symmetry_label 


    def gen_bondlist(self, bonds, newbonds):
        """
        generate all possible combinations of bonds with newbonds.
        """
        bondlist = list(itertools.product(bonds, newbonds))
        pop = []
        for id, bondpair in enumerate(bondlist):
            # check for bond compatibility
            if not self.spec_compatible(bondpair[0], bondpair[1]):
                pop.append(id)

        # remove illegal bonds from list
        pop.sort()
        [bondlist.pop(i) for i in reversed(pop)]
        return bondlist

    def spec_compatible(self, bond, newbond):
        """
        Determines if two connect_points are compatible
        1) checks to see if both have the same special/constraint flag, which 
            should be an [int] or [None]
        2) if [None] then make sure both are not metal bonding units
        
        otherwise return False.
        """
        # check if the bond is already bonded
        if bond.bonded:
            return False
        if (bond.special == newbond.constraint) or (
            newbond.special == bond.constraint):
            if bond.special is None and newbond.special is None and \
                    bond.metal == newbond.metal:
                return False
            return True
        else:
            return False

    def random_insert(self, bu_db):
        """ selects a building unit at random to seed growth """
        bu = deepcopy(choice(bu_db))
        bu.order = 0
        seed = Structure()
        debug("Inserted %s as the initial seed for the structure"
                %(bu.name))
        seed.building_units.append(bu)
        for atom in bu.atoms:
            atom.index = seed.natoms
            seed.natoms += 1
        for bond in bu.bonds:
            # append the building units' bonds to the master
            # bonding table.  This will be used to write the 
            # cif file.
            if not isinstance(bond.to, ConnectPoint) and not \
                    isinstance(bond.frm, ConnectPoint):
                seed.bonds.append((bond.frm.index, bond.to.index,
                                   bond.type, bond.distance))
        # set building unit order = 0 for the connectpoints 
        for bu in seed.building_units:
            for bond in bu.connect_points:
                bond.bu_order = 0
        seed.debug_xyz()
        return [seed]

def valid_bond(bu, cp, bu2, cp2):
    if not cp.bonded and not cp2.bonded:
        if (cp.special == cp2.constraint) or (cp.constraint
            == cp2.special):
            if cp.special == None:
                if bu.metal == bu2.metal:
                    return False
                return True
            else:
                return True

    return False

def write_debug_xyz(structure, count=[]):
    if len(count) == 0:
        file=open("debug.xyz", "w")
    else:
        file=open("debug.xyz", "a")
    count.append(1)
    file.writelines(structure.debug_lines)
    file.close()

def main():
    Log()
    if len(sys.argv) > 1:
        file = sys.argv[1]
    else:
        file = "testdb"
    data = Database(file)
    Generate(data)

if __name__ == '__main__':
    main()
