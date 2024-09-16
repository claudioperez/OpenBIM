#===----------------------------------------------------------------------===#
#
#         STAIRLab -- STructural Artificial Intelligence Laboratory
#
#===----------------------------------------------------------------------===#
#
# Certain operations are loosley adapted from:
#    https://github.com/XunXun-Zhou/Sap2OpenSees/blob/main/STO_ver1.0.py
#
import re
import sys
import warnings
from dataclasses import dataclass

import numpy as np

from .parse import load
from .utility import UnimplementedInstance, find_row, find_rows, print_log
from .frame import create_frames
from .link import create_links

RE = {
    "joint_key": re.compile("Joint[0-9]")
}

CONFIG = {
    "Frame": {
        "Taper": "Subdivide", # Integrate
        "Element": "PrismFrame",
    }
}

TYPES = {
    "Shell": {
        "Elastic": "ShellMITC4",
    },
    "Frame": {
        "Elastic": "PrismFrame"
    }
}

class _Material:
    @dataclass
    class _Steel:
        Fy:    float
        Fu:    float
        EffFy: float

class _Model:
    pass

class _Section:
    def __init__(self, name: str, csi: dict,
                 index: int, model, library):
        self.index = index
        self.name = name
        self.integration = []

        self._create(csi, model, library, None)

    def _create(self, csi, model, library, config):
        pass


class _ShellSection(_Section):
    def _create(self, csi, model, library, config):

        section = find_row(csi["AREA SECTION PROPERTIES"],
                           Section=self.name
        )

        if section is None:
            print(self.name)

        material = find_row(csi["MATERIAL PROPERTIES 01 - GENERAL"],
                            Material=section["Material"]
        )

        material = find_row(csi["MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES"],
                            Material=section["Material"]
        )
        model.section("ElasticMembranePlateSection", self.index,
                      material["E1"],  # E
                      material["G12"]/(2*material["E1"]) - 1, # nu
                      section["Thickness"],
                      material["UnitMass"]
        )
        self.integration.append(self.index)


def _create_frame_sections(csi, model, library):
    tag = 1
    for assign in csi.get("FRAME SECTION ASSIGNMENTS", []):

        if assign["AnalSect"] not in library["frame_sections"]:

            library["frame_sections"][assign["AnalSect"]] = \
              _FrameSection(assign["AnalSect"], csi, tag, model, library)

            tag += len(library["frame_sections"][assign["AnalSect"]].integration)

    return tag


class _FrameSection(_Section):
    polygon: list

    def _create(self, csi, model, library, config=None):

        self.polygon = []

        section = find_row(csi["FRAME SECTION PROPERTIES 01 - GENERAL"],
                           SectionName=self.name
        )

        segments = find_rows(csi.get("FRAME SECTION PROPERTIES 05 - NONPRISMATIC",[]),
                             SectionName=section["SectionName"])

        if section is None:
            print(csi["FRAME SECTION PROPERTIES 01 - GENERAL"])
            raise Exception(f"{self.name = }")

        if section["Shape"] not in {"Nonprismatic"}:
            material = find_row(csi["MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES"],
                                Material=section["Material"]
            )

            if "G12" in material:
                model.section("FrameElastic", self.index,
                              A  = section["Area"],
                              Ay = section["AS2"],
                              Az = section["AS2"],
                              Iz = section["I33"],
                              Iy = section["I22"],
                              J  = section["TorsConst"],
                              E  = material["E1"],
                              G  = material["G12"]
                )
                self.integration.append(self.index)


        elif section["Shape"] == "Nonprismatic" and \
             len(segments) != 1: #section["NPSecType"] == "Advanced":

            # TODO: Just treating as normal prismatic section

            assert all(segment["StartSect"] == segment["EndSect"] for segment in segments)

            if segments[0]["StartSect"] not in library:
                library[segments[0]["StartSect"]] = \
                        _FrameSection(segments[0]["StartSect"], csi, self.index, model, library)
            self.integration.append(self.index)


        # 
        elif section["Shape"] == "Nonprismatic" and \
             len(segments) == 1: #section["NPSecType"] == "Default":

            segments = find_rows(csi["FRAME SECTION PROPERTIES 05 - NONPRISMATIC"],
                                 SectionName=section["SectionName"])


            assert len(segments) == 1
            segment = segments[0]

            # Create property interpolation
            def interpolate(point, prop):
                si = find_row(csi["FRAME SECTION PROPERTIES 01 - GENERAL"],
                                   SectionName=segment["StartSect"]
                )
                sj = find_row(csi["FRAME SECTION PROPERTIES 01 - GENERAL"],
                                   SectionName=segment["EndSect"]
                )
                # TODO: Taking material from first section assumes si and sj have the same
                # material
                material = find_row(csi["MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES"],
                                    Material=si["Material"]
                )

                if prop in material:
                    start= end = material[prop]
                else:
                    start = si[prop]
                    end = sj[prop]

                power = {
                        "Linear":    1,
                        "Parabolic": 2,
                        "Cubic":     3
                }[segment.get(f"E{prop}Var", "Linear")]

                return start*(1 + point*((end/start)**(1/power)-1))**power
            

            # Define a numerical integration scheme

            from numpy.polynomial.legendre import leggauss
            nip = 5
            off = 1
            for x,wi in zip(*leggauss(nip)):
                xi = (1+x)/2

                model.section("FrameElastic", self.index+off,
                              A  = interpolate(xi, "Area"),
                              Ay = interpolate(xi, "AS2"),
                              Az = interpolate(xi, "AS2"),
                              Iz = interpolate(xi, "I33"),
                              Iy = interpolate(xi, "I22"),
                              J  = interpolate(xi, "TorsConst"),
                              E  = interpolate(xi, "E1"),
                              G  = interpolate(xi, "G12")
                )


                self.integration.append((self.index+off, xi, wi/2))

                off += 1


        else:
            warnings.warn(f"Unknown shape {section['Shape']}")
            # TODO: truss section?
            pass

        # TODO
        outline = "FRAME SECTION PROPERTIES 06 - POLYGON DATA"


class _Shell:
    def __init__(self, csi):
        pass




def _collect_materials(csi, model):
    library = {
      "frame_sections": {},
      "shell_sections": {},
      "link_materials": {},
    }

    # 1) Material

    #
    # 2) Links
    #
    mat_total = 1

    "LINK PROPERTY DEFINITIONS 02 - LINEAR",
    "LINK PROPERTY DEFINITIONS 03 - MULTILINEAR",
    "LINK PROPERTY DEFINITIONS 05 - GAP",
    "LINK PROPERTY DEFINITIONS 06 - HOOK",
    "LINK PROPERTY DEFINITIONS 07 - RUBBER ISOLATOR",
    "LINK PROPERTY DEFINITIONS 08 - SLIDING ISOLATOR",
    "LINK PROPERTY DEFINITIONS 11 - MULTILINEAR PLASTIC",

    for damper in csi.get("LINK PROPERTY DEFINITIONS 04 - DAMPER", []):
        continue
        name = damper["Link"]
#       dof = damper["DOF"]
        stiff = damper["TransK"]
        dampcoeff = damper["TransC"]
        exp = damper["CExp"]
        model.eval(f"uniaxialMaterial ViscousDamper {mat_total} {stiff} {dampcoeff}' {exp}\n")

        library["link_materials"][name] = mat_total
        mat_total += 1

    for link in csi.get("LINK PROPERTY DEFINITIONS 10 - PLASTIC (WEN)", []):
#       continue
        name = link["Link"]
        dof = link["DOF"]

        if not link.get("Nonlinear", False):
            stiff = link["TransKE"]
            model.eval(f"uniaxialMaterial Elastic {mat_total} {stiff}\n")
        else:
            stiff = link["TransK"]
            fy    = link["TransYield"]
            exp   = link["YieldExp"] # TODO
            ratio = link["Ratio"]
            model.eval(f"uniaxialMaterial Steel01 {mat_total} {fy} {stiff} {ratio}\n")

        library["link_materials"][name] = mat_total
        mat_total += 1


    # 2) Frame
    tag = _create_frame_sections(csi, model, library)


    # 3) Shell
    for assign in csi.get("AREA SECTION ASSIGNMENTS", []):
        if assign["Section"] not in library["shell_sections"]:
            library["shell_sections"][assign["Section"]] = \
              _ShellSection(assign["Section"], csi, tag, model, library)
            tag += len(library["shell_sections"][assign["Section"]].integration)

    return library


def create_model(sap, types=None, verbose=False):

    import opensees.openseespy as ops

    used = {
        "TABLES AUTOMATICALLY SAVED AFTER ANALYSIS"
    }
    log = []

    config = CONFIG

    #
    # Create model
    #
    dofs = {key for key,val in sap["ACTIVE DEGREES OF FREEDOM"][0].items() if val}
    dims = {key for key,val in sap["ACTIVE DEGREES OF FREEDOM"][0].items() if val}
    ndf = sum(int(i) for i in sap["ACTIVE DEGREES OF FREEDOM"][0].values())
    ndm = sum(int(v) for k,v in sap["ACTIVE DEGREES OF FREEDOM"][0].items()
              if k[0] == "U")
    # ndm = 3 if sap["ACTIVE DEGREES OF FREEDOM"][0]["UZ"] else 2
    # print(ndm)

    model = ops.Model(ndm=ndm, ndf=ndf)

    used.add("ACTIVE DEGREES OF FREEDOM")

    #
    # Create nodes
    #
    dofs = [f"U{i}" for i in range(1, ndm+1)]
    if ndm == 3:
        dofs = dofs + ["R1", "R2", "R3"]
    else:
        dofs = dofs + ["R3"]
    for node in sap["JOINT COORDINATES"]:
        model.node(node["Joint"], tuple(node[i] for i in ("XorR", "Y", "Z") if i in node))
    for node in sap.get("JOINT RESTRAINT ASSIGNMENTS", []):
        model.fix(node["Joint"], tuple(int(node[i]) for i in dofs))

    if True:
        # TODO
        # The format of body dictionary is {'node number':'constraint name'}
        constraints = {}

        for constraint in  sap.get("JOINT CONSTRAINT ASSIGNMENTS", []):
            # print(constraint)
            if "Type" in constraint and constraint["Type"] == "Body":
                # map node number to constraint
                constraints[constraint["Joint"]] = constraint["Constraint"]
            else:
                log.append(UnimplementedInstance("Joint.Constraint", constraint))

        # Sort the dictionary by body name and return a list [(node, body name)]
        constraints = list(sorted(constraints.items(), key=lambda x: x[1]))


        if len(constraints) > 0:
            nodes = []
            # Assign the first body name to the pointer
            pointer = constraints[0][1]

            # Traverse the tuple. If the second element in the tuple, the body
            # name, is the same as the pointer, then store the node number, 
            # into nodes.
            for node, constraint in constraints:
                if constraint == pointer:
                    nodes.append(node)
                else:
                    # First write the nodes in nodes to the body file
                    for le in range(len(nodes)-1):
                        model.eval(f"rigidLink beam {nodes[0]} {nodes[le + 1]}\n")
                    # Restore nodes and save the node that returns False.
                    nodes = []
                    nodes.append(node)
                    # The pointer is changed to the new body name
                    pointer = constraint

            # After the for loop ends, write the nodes in the nodes of the last loop to the body file.
            for le in range(len(nodes)-1):
                model.eval(f"rigidLink beam {nodes[0]} {nodes[le + 1]}\n")


    used.add("JOINT COORDINATES")
    used.add("JOINT RESTRAINT ASSIGNMENTS")

    # TODO

    library = _collect_materials(sap, model)


    # Unimplemented objects
    for item in [
        "CONNECTIVITY - CABLE",
        "CONNECTIVITY - LINK",
        "CONNECTIVITY - SOLID",
        "CONNECTIVITY - TENDON"]:
        for elem in sap.get(item, []):
            log.append(UnimplementedInstance(item, elem))

    #
    # Create Links
    #
    log.extend(create_links(sap, model, library, config))


    #
    # Create frames
    #
    log.extend(create_frames(sap, model, library, config))

    
    #
    # Create shells
    #
    for shell in sap.get("CONNECTIVITY - AREA", []):
        if "AREA ADDED MASS ASSIGNMENTS" in sap:
            row = find_row(sap["AREA ADDED MASS ASSIGNMENTS"],
                           Area=shell["Area"])
            if row:
                mass = row["MassPerArea"]
            else:
                mass = 0.0
        else:
            mass = 0.0

        # Find section
        assign  = find_row(sap["AREA SECTION ASSIGNMENTS"],
                           Area=shell["Area"])

        section = library["shell_sections"][assign["Section"]].index

        nodes = tuple(v for k,v in shell.items() if RE["joint_key"].match(k))

        if len(nodes) == 4:
            type = TYPES["Shell"]["Elastic"]

        elif len(nodes) == 3:
            type = "ShellNLDKGT"

        model.element(type, None,
                      nodes, section
        )

    if verbose:
        print_log(log)

    if verbose and False:
        for table in sap:
            if table not in used:
                print(f"\t{table}", file=sys.stderr)

    return model



