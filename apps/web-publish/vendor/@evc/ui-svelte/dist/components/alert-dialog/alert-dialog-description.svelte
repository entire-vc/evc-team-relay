<script lang="ts">
  import { AlertDialog as AlertDialogPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = AlertDialogPrimitive.DescriptionProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<AlertDialogPrimitive.Description
  class={cn('text-sm text-muted-foreground', className)}
  {...restProps}
>
  {@render children?.()}
</AlertDialogPrimitive.Description>
