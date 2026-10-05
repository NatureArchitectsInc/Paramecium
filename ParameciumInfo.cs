using Grasshopper;
using Grasshopper.Kernel;
using System;
using System.Drawing;

namespace Paramecium
{
    public class ParameciumInfo : GH_AssemblyInfo
    {
        public override string Name => "Paramecium";

        //Return a 24x24 pixel bitmap to represent this GHA library.
        public override Bitmap Icon => Properties.Resource.paramecium_24;

        //Return a short string describing the purpose of this GHA library.
        public override string Description => "Grasshopper plugin for editing parametric parts via the FreeCAD UI.";

        public override Guid Id => new Guid("78fc26c7-1c8a-408f-b277-86f78abb4820");

        //Return a string identifying you or your company.
        public override string AuthorName => "";

        //Return a string representing your preferred contact details.
        public override string AuthorContact => "";

        //Return a string representing the version.  This returns the same version as the assembly.
        public override string AssemblyVersion => GetType().Assembly.GetName().Version.ToString();
    }

    // Registers the tab icon shown in the Grasshopper ribbon.
    public class ParameciumCategoryIcon : GH_AssemblyPriority
    {
        public override GH_LoadingInstruction PriorityLoad()
        {
            Instances.ComponentServer.AddCategoryIcon("Paramecium", Properties.Resource.paramecium_16);
            Instances.ComponentServer.AddCategorySymbolName("Paramecium", 'P');
            return GH_LoadingInstruction.Proceed;
        }
    }
}