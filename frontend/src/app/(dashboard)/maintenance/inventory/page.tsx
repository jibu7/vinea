'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Package, MapPin, Settings, Tags } from "lucide-react";
import Link from "next/link";

export default function InventoryMaintenancePage() {
  const setupItems = [
    {
      title: "Item Master",
      description: "Create and manage inventory items and products",
      icon: Package,
      href: "/maintenance/inventory/items",
      color: "text-blue-600"
    },
    {
      title: "Warehouses & Locations",
      description: "Setup storage locations and warehouse management",
      icon: MapPin,
      href: "/maintenance/inventory/locations",
      color: "text-green-600"
    },
    {
      title: "Categories & Classifications",
      description: "Organize items with categories and attributes",
      icon: Tags,
      href: "/maintenance/inventory/categories",
      color: "text-purple-600"
    },
    {
      title: "Inventory Configuration",
      description: "Setup costing methods and inventory policies",
      icon: Settings,
      href: "/maintenance/inventory/config",
      color: "text-orange-600"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Inventory Setup</h1>
        <p className="text-muted-foreground">
          Configure inventory items, locations, and management policies
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-2">
        {setupItems.map((item) => {
          const IconComponent = item.icon;
          return (
            <Card key={item.href} className="hover:shadow-md transition-shadow">
              <CardHeader className="flex flex-row items-center space-y-0 pb-2">
                <div className={`p-2 rounded-lg bg-gray-100 ${item.color}`}>
                  <IconComponent className="h-6 w-6" />
                </div>
                <div className="ml-4">
                  <CardTitle className="text-base">{item.title}</CardTitle>
                  <CardDescription className="text-sm">
                    {item.description}
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <Link href={item.href}>
                  <Button variant="outline" className="w-full">
                    Configure
                  </Button>
                </Link>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Setup Checklist</CardTitle>
          <CardDescription>
            Essential inventory setup tasks to complete before processing transactions
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              <span>Configure inventory accounts in Chart of Accounts</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
              <span>Set up warehouse locations and storage areas</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
              <span>Create item categories and classification structure</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-red-500 rounded-full"></div>
              <span>Define costing methods and reorder policies</span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
