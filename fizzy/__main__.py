"""CLI: python -m fizzy import FILE.csv | backup DIR | sync | device-link NAME | clear-pin | maintenance on [MESSAGE] | maintenance off"""
import sys

from . import config, customers, db, devices, pin, square


def main(argv):
    conn = db.connect()
    db.init(conn)
    if len(argv) == 2 and argv[0] == "import":
        with open(argv[1], encoding="utf-8-sig") as f:
            rows, errors = customers.parse_directory_csv(f.read())
        for e in errors:
            print(e)
        print(customers.import_rows(conn, rows, "cli"))
        conn.commit()
    elif len(argv) == 2 and argv[0] == "backup":
        print(db.backup(conn, argv[1], config.BACKUP_KEEP))
    elif len(argv) == 2 and argv[0] == "device-link":
        print(devices.create_link(conn, argv[1], "cli"))
        print(f"Open it on the device within {devices.LINK_MINUTES} minutes.")
        conn.commit()
    elif argv == ["clear-pin"]:
        pin.set_pin(conn, "", "cli")
        conn.commit()
        print("Admin PIN removed.")
    elif argv[:2] == ["maintenance", "on"]:
        db.set_setting(conn, "maintenance", " ".join(argv[2:]))
        conn.commit()
        print("Maintenance mode on: everyone sees the 'back soon' page (Square sales still come in).")
        print("Turn it off with: python -m fizzy maintenance off")
    elif argv == ["maintenance", "off"]:
        conn.execute("DELETE FROM settings WHERE key = 'maintenance'")
        conn.commit()
        print("Maintenance mode off.")
    elif argv == ["sync"]:
        print(square.sync(conn))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
