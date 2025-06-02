'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { BarChart3, Package, MapPin, TrendingUp } from "lucide-react";
import Link from "next/link";

export default function InventoryReportsPage() {
  const reportCategories = [
    {
      title: "Valuation Reports",
      description: "Inventory valuation and costing analysis",
      icon: TrendingUp,
      reports: [
        { name: "Inventory Valuation", href: "/reports/inventory/valuation" },
        { name: "Cost Analysis", href: "/reports/inventory/cost-analysis" },
        { name: "ABC Analysis", href: "/reports/inventory/abc-analysis" }
      ],
      color: "text-blue-600"
    },
    {
      title: "Stock Reports",
      description: "Stock levels and movement analysis",
      icon: Package,
      reports: [
        { name: "Stock Status Report", href: "/reports/inventory/stock-status" },
        { name: "Reorder Report", href: "/reports/inventory/reorder" },
        { name: "Dead Stock Report", href: "/reports/inventory/dead-stock" }
      ],
      color: "text-green-600"
    },
    {
      title: "Location Reports",
      description: "Warehouse and location analysis",
      icon: MapPin,
      reports: [
        { name: "Stock by Location", href: "/reports/inventory/by-location" },
        { name: "Location Utilization", href: "/reports/inventory/location-util" },
        { name: "Transfer History", href: "/reports/inventory/transfers" }
      ],
      color: "text-purple-600"
    },
    {
      title: "Movement Reports",
      description: "Transaction and movement history",
      icon: BarChart3,
      reports: [
        { name: "Transaction History", href: "/reports/inventory/transactions" },
        { name: "Movement Summary", href: "/reports/inventory/movements" },
        { name: "Adjustment Report", href: "/reports/inventory/adjustments" }
      ],
      color: "text-orange-600"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Inventory Reports</h1>
        <p className="text-muted-foreground">
          Analyze stock levels, movements, valuations, and inventory performance
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {reportCategories.map((category) => {
          const IconComponent = category.icon;
          return (
            <Card key={category.title} className="hover:shadow-md transition-shadow">
              <CardHeader className="flex flex-row items-center space-y-0 pb-2">
                <div className={`p-2 rounded-lg bg-gray-100 ${category.color}`}>
                  <IconComponent className="h-6 w-6" />
                </div>
                <div className="ml-4">
                  <CardTitle className="text-base">{category.title}</CardTitle>
                  <CardDescription className="text-sm">
                    {category.description}
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {category.reports.map((report) => (
                    <Link key={report.href} href={report.href}>
                      <Button variant="ghost" className="w-full justify-start text-sm">
                        {report.name}
                      </Button>
                    </Link>
                  ))}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Quick Stats</CardTitle>
          <CardDescription>Current inventory metrics and KPIs</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-600">$124,850</div>
              <div className="text-sm text-muted-foreground">Total Value</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-600">1,247</div>
              <div className="text-sm text-muted-foreground">Total Items</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-purple-600">15</div>
              <div className="text-sm text-muted-foreground">Low Stock Items</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-orange-600">8.5</div>
              <div className="text-sm text-muted-foreground">Turns/Year</div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
